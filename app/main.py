import re
import logging
import uuid
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.observability import initialize
from app.demo_context import principal, Principal
from app.aws import client
from app.config import settings
from app.vector import delete_document as delete_vectors
from app.db import connection, audit
from app.rag import chat
from app.guardrails import check_input, classify_input
from app.memory import history, save_turn, list_messages
from worker.ingest import process_s3_key

initialize()


BASE=Path(__file__).resolve().parent
app=FastAPI(title='Atlas Enterprise RAG',docs_url='/api/docs')
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
templates=Jinja2Templates(directory=BASE/'templates')
log=logging.getLogger(__name__)



class Upload(BaseModel):
    filename:str=Field(min_length=1,max_length=180)

class Ask(BaseModel):
    question:str=Field(min_length=3,max_length=2000)
    conversation_id:uuid.UUID|None=None

class Feedback(BaseModel):
    document_id:str|None=None
    rating:int=Field(ge=-1,le=1)
    note:str=Field(default='',max_length=500)



@app.get('/',response_class=HTMLResponse)
def home(request:Request):
    return templates.TemplateResponse('index.html',{'request':request,'region':settings().aws_region,
        'mode':'single-workspace demo'})



@app.get('/health/live')
def live(): return {'status':'alive'}



@app.get('/health/ready')
def ready():
    try:
        with connection() as conn: conn.execute('SELECT 1').fetchone()
        return {'status':'ready'}
    except Exception as exc:
        raise HTTPException(503,'Database unavailable') from exc



@app.post('/api/documents/uploads')
def create_upload(body:Upload,p:Principal=Depends(principal)):
    suffix=Path(body.filename).suffix.lower()
    if suffix not in {'.pdf','.docx','.txt','.md'}:
        raise HTTPException(400,'Allowed types: PDF, DOCX, TXT, MD')
    if not settings().s3_bucket:
        raise HTTPException(503,'Document storage is not configured on this server (S3_BUCKET is missing).')
    doc_id=str(uuid.uuid4())
    key=f'tenants/{p.tenant}/incoming/{doc_id}/v1{suffix}'
    try:
        url=client('s3').generate_presigned_url('put_object',Params={'Bucket':settings().s3_bucket,
            'Key':key,'ContentType':'application/octet-stream'},ExpiresIn=300)
    except Exception as exc:
        log.exception('Could not create a presigned document upload URL')
        raise HTTPException(503,'Document storage credentials or configuration are unavailable.') from exc
    with connection() as conn:
        conn.execute('''INSERT INTO documents(id,tenant_id,title,owner_sub,allowed_groups,s3_key,status,current_version)
                        VALUES (%s,%s,%s,%s,%s,%s,'pending',1)''',
                        (doc_id,p.tenant,body.filename,p.subject,["workspace"],key))
        audit(conn,p.subject,p.tenant,'upload_requested',{'document_id':doc_id})
    # Browser PUTs to S3; a separate completion request triggers synchronous ingestion.
    return {'document_id':doc_id,'upload_url':url,'headers':{'Content-Type':'application/octet-stream'}}


class CompleteUpload(BaseModel):
    document_id:uuid.UUID

@app.post('/api/documents/complete')
def complete_upload(body:CompleteUpload,p:Principal=Depends(principal)):
    with connection() as conn:
        row=conn.execute("""SELECT s3_key,status FROM documents WHERE id=%s AND tenant_id=%s""",
                        (body.document_id,p.tenant)).fetchone()
    if not row: raise HTTPException(404,'Document not found')
    if row['status']=='ready': return {'status':'ready'}
    if row['status']=='processing': raise HTTPException(409,'Already processing')
    try:
        process_s3_key(row['s3_key'])
    except Exception as exc:
        raise HTTPException(502,'Ingestion failed. Check server logs and document status.') from exc
    return {'status':'ready'}



@app.get('/api/documents')
def documents(p:Principal=Depends(principal)):
    with connection() as conn:
        rows=conn.execute('''SELECT id,title,status,current_version,created_at,allowed_groups FROM documents
            WHERE tenant_id=%s AND (allowed_groups && %s::text[] OR owner_sub=%s)
            ORDER BY created_at DESC LIMIT 100''',(p.tenant,list(p.groups),p.subject)).fetchall()
    return {'documents':rows}


def _delete_document_data(document_id,tenant,actor):
    with connection() as conn:
        row=conn.execute('''SELECT id,title,owner_sub,s3_key,status FROM documents
            WHERE id=%s AND tenant_id=%s FOR UPDATE''',(document_id,tenant)).fetchone()
        if not row: raise RuntimeError('Document no longer exists')
        if row['status']=='processing':
            raise RuntimeError('Document processing is still in progress')
        conn.execute("UPDATE documents SET status='retired' WHERE id=%s AND tenant_id=%s",
                     (document_id,tenant))

    delete_vectors(tenant,str(document_id))
    client('s3').delete_object(Bucket=settings().s3_bucket,Key=row['s3_key'])

    with connection() as conn:
        conn.execute('DELETE FROM documents WHERE id=%s AND tenant_id=%s',(document_id,tenant))
        audit(conn,actor,tenant,'document_deleted',{'document_id':str(document_id)})


@app.post('/api/documents/{document_id}/delete-request')
def request_document_deletion(document_id:uuid.UUID,p:Principal=Depends(principal)):
    with connection() as conn:
        doc=conn.execute('''SELECT id,title,owner_sub,status FROM documents
            WHERE id=%s AND tenant_id=%s FOR UPDATE''',(document_id,p.tenant)).fetchone()
        if not doc: raise HTTPException(404,'Document not found')
        if not p.admin and doc['owner_sub']!=p.subject:
            raise HTTPException(403,'Only the document owner or an administrator can request deletion')
        if doc['status']=='processing':
            raise HTTPException(409,'Wait for document processing to finish before requesting deletion')
        existing=conn.execute('''SELECT id FROM approvals WHERE tenant_id=%s AND action='delete_document'
            AND document_id=%s AND status='pending' ORDER BY created_at DESC LIMIT 1''',
            (p.tenant,document_id)).fetchone()
        if existing:
            return {'approval_id':str(existing['id']),'status':'pending'}
        approval=conn.execute('''INSERT INTO approvals
            (tenant_id,requester,question,action,document_id,status)
            VALUES (%s,%s,%s,'delete_document',%s,'pending') RETURNING id''',
            (p.tenant,p.subject,f'Delete "{doc["title"]}" from S3 and Pinecone',document_id)).fetchone()
        audit(conn,p.subject,p.tenant,'document_deletion_requested',
              {'document_id':str(document_id),'approval_id':str(approval['id'])})
    return {'approval_id':str(approval['id']),'status':'pending'}




@app.post('/api/chat')
def ask(body:Ask,p:Principal=Depends(principal)):
    if not settings().groq_api_key:
        raise HTTPException(503,'Chat is not configured on this server (GROQ_API_KEY is missing).')
    if not settings().pinecone_api_key or not settings().pinecone_index_name:
        raise HTTPException(503,'Document search is not configured on this server (Pinecone settings are missing).')
    reason=classify_input(body.question)
    if reason:
        with connection() as conn:
            audit(conn,p.subject,p.tenant,'guardrail_input_blocked',{'reason':reason})
        raise HTTPException(400,f'Input guardrail blocked this request ({reason}).')
    if settings().hitl_enabled and any(x in body.question.lower() for x in ('delete','export','send to','update policy')):
        with connection() as conn:
            row=conn.execute('INSERT INTO approvals(tenant_id,requester,question,status) VALUES (%s,%s,%s,%s) RETURNING id',(p.tenant,p.subject,body.question,'pending')).fetchone()
            audit(conn,p.subject,p.tenant,'approval_requested',{'id':str(row['id'])})
        return {'answer':'This request requires administrator approval before any action. Open the Approvals tab to review it.','sources':[],'status':'pending_approval','approval_id':str(row['id'])}
    conversation_id=body.conversation_id or uuid.uuid4()
    try:
        prior=history(p.tenant,conversation_id)
        result=chat(p,body.question,prior)
        save_turn(p.tenant,conversation_id,body.question,result['answer'])
    except Exception as exc:
        log.exception('Chat request failed')
        raise HTTPException(502,'Chat backend failed. Check the application logs for the failing dependency.') from exc
    if result.get('answer')=='The response was blocked by the sensitive-data output guardrail.':
        with connection() as conn:
            audit(conn,p.subject,p.tenant,'guardrail_output_blocked',{'conversation_id':str(conversation_id)})
        result['status']='guardrail_blocked'
        result['sources']=[]
    return {**result,'conversation_id':str(conversation_id)}

@app.get('/api/conversations/{conversation_id}/messages')
def conversation_messages(conversation_id:uuid.UUID,p:Principal=Depends(principal)):
    return {'conversation_id':str(conversation_id),'messages':list_messages(p.tenant,conversation_id)}

@app.post('/api/feedback')
def feedback(body:Feedback,p:Principal=Depends(principal)):
    with connection() as conn:
        conn.execute('INSERT INTO feedback(tenant_id,actor,rating,note) VALUES (%s,%s,%s,%s)',
                    (p.tenant,p.subject,body.rating,body.note))
    return {'saved':True}

@app.get('/api/admin/metrics')
def metrics(p:Principal=Depends(principal)):
    with connection() as conn:
        docs=conn.execute('SELECT status,count(*) AS total FROM documents WHERE tenant_id=%s GROUP BY status',(p.tenant,)).fetchall()
        fb=conn.execute('SELECT rating,count(*) AS total FROM feedback WHERE tenant_id=%s GROUP BY rating',(p.tenant,)).fetchall()
    return {'documents':docs,'feedback':fb}



#HITL (Human-in-the-loop) approval endpoints for admin users to review and approve or reject requests that require elevated permissions or actions.
class ApprovalDecision(BaseModel):
    approve:bool
    
@app.get('/api/approvals')
def list_approvals(p:Principal=Depends(principal)):
    if not p.admin: raise HTTPException(403,'Admin only')
    with connection() as conn:
        rows=conn.execute("SELECT id,requester,question,action,document_id,status,reviewer,created_at,reviewed_at FROM approvals WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100",(p.tenant,)).fetchall()
    return {'approvals':rows}

@app.post('/api/approvals/{approval_id}/decision')
def decide(approval_id:uuid.UUID,body:ApprovalDecision,p:Principal=Depends(principal)):
    if not p.admin: raise HTTPException(403,'Admin only')
    with connection() as conn:
        row=conn.execute("SELECT id,action,document_id FROM approvals WHERE id=%s AND tenant_id=%s AND status='pending' FOR UPDATE",(approval_id,p.tenant)).fetchone()
        if not row: raise HTTPException(404,'Pending approval not found')
        decision='approved' if body.approve else 'rejected'
        conn.execute('UPDATE approvals SET status=%s,reviewer=%s,reviewed_at=now() WHERE id=%s',
                     (decision,p.subject,approval_id))
        audit(conn,p.subject,p.tenant,'approval_decided',{'id':str(approval_id),'approved':body.approve})
    if not body.approve:
        return {'status':'rejected','executed':False}
    if row['action']!='delete_document' or not row['document_id']:
        return {'status':'approved','executed':False}
    try:
        _delete_document_data(row['document_id'],p.tenant,p.subject)
    except Exception as exc:
        log.exception('Approved document deletion failed for approval %s',approval_id)
        with connection() as conn:
            conn.execute("UPDATE approvals SET status='execution_failed' WHERE id=%s AND tenant_id=%s",
                         (approval_id,p.tenant))
            audit(conn,p.subject,p.tenant,'document_deletion_failed',
                  {'document_id':str(row['document_id']),'approval_id':str(approval_id)})
        return {'status':'execution_failed','executed':False,
                'message':'Deletion failed. Check storage credentials and retry with a new approval.'}
    with connection() as conn:
        conn.execute("UPDATE approvals SET status='executed' WHERE id=%s AND tenant_id=%s",
                     (approval_id,p.tenant))
        audit(conn,p.subject,p.tenant,'document_deletion_executed',
              {'document_id':str(row['document_id']),'approval_id':str(approval_id)})
    return {'status':'executed','executed':True}

@app.get('/api/admin/guardrails')
def guardrail_metrics(p:Principal=Depends(principal)):
    if not p.admin: raise HTTPException(403,'Admin only')
    with connection() as conn:
        rows=conn.execute("""SELECT action,count(*) AS total FROM audit_events
            WHERE tenant_id=%s AND action IN ('guardrail_input_blocked','guardrail_output_blocked')
            GROUP BY action ORDER BY action""",(p.tenant,)).fetchall()
    return {'events':rows,'mode':'deterministic_demo','managed_bedrock_guardrails':False}
