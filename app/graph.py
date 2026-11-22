from app.observability import trace
from typing import TypedDict,Any
from langgraph.graph import StateGraph,START,END
from app.aws import embed,answer
from app.vector import search
from app.config import settings
from app.guardrails import check_input,check_output
import re


class State(TypedDict,total=False):
    question:str # The user's question
    principal:Any # Info regarding the user making the request, including tenant and groups
    passages:list # The retrieved passages from the vector database that are relevant to the question
    answer:str #  generated answer from the retrieved passages and the user's question
    sources:list # The sources used to generate the answer
    status:str #  status of the request
    history:list #  conversation history

#s is the current LangGraph state.
@trace("guard")
def guard(s): return {'question':check_input(s['question'])}


@trace("retrieve")
def retrieve(s):
    p=s['principal']
    # Keep standalone questions intact. Only add context for follow-ups that
    # cannot be interpreted without the preceding turn.
    prior_questions=[m['content'] for m in s.get('history',[]) if m['role']=='user']
    question=s['question'].strip()
    word_count=len(question.split())
    needs_context = word_count <= 6 and bool(re.search(
        r"\b(it|that|those|they|them|there|this|these|one|ones)\b|^(what about|how about|and|also)\b",
        question,
        re.IGNORECASE,
    ))
    needs_context = needs_context or (
        word_count <= 3 and bool(re.fullmatch(r"(why|where|when|how|what)[?.!]*", question, re.IGNORECASE))
    )
    query=(prior_questions[-1]+'\nFollow-up: '+question) if prior_questions and needs_context else question

    hits=search(p.tenant,embed(query),p.groups,p.subject)
    hits=[h for h in hits if h['score']>=settings().min_relevance]
    
    sources = [
    {
        'index': i + 1,
        'document_id': h['metadata'].get('document_id', ''),
        'title': h['metadata'].get('title', ''),
        'page': h['metadata'].get('page', 1),
        'excerpt': h['metadata'].get('text', '')[:320]
    }
    for i, h in enumerate(hits)
]
    return {'passages':hits,'sources':sources}



def route(s): return 'generate' if s['passages'] else 'no_evidence'


@trace("no_evidence")
def no_evidence(s): return {'answer':'I could not find supporting evidence in documents you are authorized to access.','status':'no_evidence'}


@trace("generate")
def generate(s):
    evidence = "\n\n".join(
        f'[{i + 1}] {h["metadata"].get("title", "Document")}, '
        f'page {h["metadata"].get("page", 1)}: '
        f'{h["metadata"].get("text", "")}'
        for i, h in enumerate(s["passages"])
    )

    return {
        "answer": answer(
            s["question"],
            evidence,
            s.get("history", [])
        ),
        "status": "answered"
    }

@trace("validate")
def validate(s): return {'answer':check_output(s['answer'])}



def make_graph():
    g=StateGraph(State)

    for name, fn in [
        ('guard', guard),
        ('retrieve', retrieve),
        ('no_evidence', no_evidence),
        ('generate', generate),
        ('validate', validate)
    ]:g.add_node(name, fn)    
    
    g.add_edge(START,'guard');
    g.add_edge('guard','retrieve')
    g.add_conditional_edges('retrieve',route,{'generate':'generate','no_evidence':'no_evidence'})
    g.add_edge('generate','validate');
    g.add_edge('validate',END);
    g.add_edge('no_evidence',END)

    return g.compile()



graph=make_graph()



@trace("chat")
def chat(p,question,history=None):
    r=graph.invoke({'principal':p,'question':question,'history':history or []})
    return {'answer':r['answer'],'sources':r.get('sources',[]),'status':r.get('status','answered')}
