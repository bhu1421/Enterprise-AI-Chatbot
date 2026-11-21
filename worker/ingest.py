#downloading (retrieving) an existing document from S3, processing it, and uploading its embeddings to Pinecone.

import io
import logging
from pathlib import Path
from docx import Document
from pypdf import PdfReader
from app.aws import client, embed
from app.vector import upsert
from app.config import settings
from app.db import connection, audit
from app.observability import trace, initialize

initialize()
#initialize() function is called once before any instrumented app modules are loaded.

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
log=logging.getLogger('atlas.worker')

#blob is the binary content of the file.
def parse(blob:bytes, suffix:str):
    if suffix=='.pdf':
        reader=PdfReader(io.BytesIO(blob))
        return [(i+1,page.extract_text() or '') for i,page in enumerate(reader.pages)]
    
    if suffix=='.docx':
        doc=Document(io.BytesIO(blob))
        return [(1,'\n'.join(p.text for p in doc.paragraphs))]
    
    return [(1,blob.decode('utf-8'))]




def chunks(text, size=1200, overlap=180):
    text=' '.join(text.split())
    pos=0
    while pos<len(text):
        yield text[pos:pos+size]
        if pos+size>=len(text): break
        pos+=size-overlap



#process_s3_key function is responsible for processing a document stored in an S3 bucket.
#It retrieves the document, checks its validity, extracts text, generates embeddings, 
#and upserts them into a Pinecone vector database.
#The function also updates the document's status in the database and logs the ingestion process.

@trace("process_s3_key")
def process_s3_key(key):  
    #key is the S3 object key, which identifies a file inside your S3 bucket(key = "tenant-001/documents/report.pdf").

    s=settings()
    with connection() as conn:
        doc=conn.execute(
            'SELECT * FROM documents WHERE s3_key=%s FOR UPDATE',
            (key,)
        ).fetchone()

        if not doc or doc['status']=='ready': return
        if doc['status'] not in ('pending','failed'): return
        conn.execute("UPDATE documents SET status='processing' WHERE id=%s",(doc['id'],))
    try:
        obj=client('s3').get_object(Bucket=s.s3_bucket,Key=key)
        if obj['ContentLength']>s.max_upload_bytes: raise ValueError('Upload too large')
        blob=obj['Body'].read(s.max_upload_bytes+1)
        if len(blob)>s.max_upload_bytes: raise ValueError('Upload too large')

        if Path(key).suffix.lower()=='.pdf' and not blob.startswith(b'%PDF'): raise ValueError('Invalid PDF')
        pages=parse(blob,Path(key).suffix.lower())

        parts=[(page,part) for page,text in pages for part in chunks(text)] #chunking the text into smaller parts for processing and embedding.
        if not parts: raise ValueError('No extractable text; scanned PDFs need Textract OCR')
        
        vectors=[]
        for i, (page, part) in enumerate(parts):
            vectors.append({
                'id': f'{doc["id"]}:{doc["current_version"]}:{i}',
                'values': embed(part),
                'metadata': {
                    'document_id': str(doc['id']),
                    'title': doc['title'],
                    'page': page,
                    'text': part,
                    'owner_sub': doc['owner_sub'],
                    'allowed_groups': doc['allowed_groups']
                }
            })
        # Keep batches small; Pinecone metadata must fit the index's limits.

        #Upload vectors to Pinecone
        for i in range(0,len(vectors),50): upsert(doc['tenant_id'],vectors[i:i+50])

        with connection() as conn:
            conn.execute("UPDATE documents SET status='ready' WHERE id=%s",(doc['id'],))
            audit(
                conn,
                doc['owner_sub'],
                doc['tenant_id'],
                'ingest_ready',
                {
                    'document_id': str(doc['id']),
                    'chunks': len(parts)
                }
            )

        log.info('Ingested document %s chunks=%d',doc['id'],len(parts))
    except Exception:
        log.exception('Ingestion failed for key %s',key)
        with connection() as conn:
            conn.execute("UPDATE documents SET status='failed' WHERE id=%s",(doc['id'],))
        raise   