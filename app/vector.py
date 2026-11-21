from app.observability import trace
import logging
import time
from functools import lru_cache
from pinecone import Pinecone, ServerlessSpec
from app.config import settings


log = logging.getLogger(__name__)

#index() function initializes a Pinecone index and returns it.
# It checks if the index exists, creates it if it doesn't, and ensures it's ready for use.
@lru_cache(maxsize=1)
def index():
    """Return an initialized Pinecone index. Safe for repeated requests in one process."""
    s = settings()
    if not s.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing from .env")
    if not s.pinecone_index_name:
        raise RuntimeError("PINECONE_INDEX_NAME is missing from .env")
    
    pc = Pinecone(api_key=s.pinecone_api_key)
    #pc is the Pinecone client, which is used to interact with the Pinecone service.

    name = s.pinecone_index_name
    
    # Describe rather than list: avoids listing every index and works for existing indexes.
    try:
        info = pc.describe_index(name)
        #pc.describe_index(name) retrieves information about the specified index, including its dimension and metric.
    except Exception as exc:
        # Only create on an actual 404; never hide auth/network failures.
        # exc is the exception raised when the index does not exist. If the exception status is not 404, it raises the exception.
        if getattr(exc, 'status', None) != 404:
            raise
        log.info('Creating Pinecone index %s (dimension=%d, metric=cosine)', name, s.embed_dim)
        try:
            pc.create_index(name=name, dimension=s.embed_dim, metric='cosine',
                spec=ServerlessSpec(cloud=s.pinecone_cloud, region=s.pinecone_region))
            #  spec specifies the cloud provider and region for the index.
        except Exception as create_exc:
            # Another process may have created the index concurrently.
            # create_exc is the exception raised when trying to create the index. If the exception status is not 409 (conflict), it raises the exception.
            if getattr(create_exc, 'status', None) != 409:
                raise
        info = pc.describe_index(name)
    if int(info.dimension) != s.embed_dim:
        raise ValueError(f'Pinecone index {name} dimension={info.dimension}, expected {s.embed_dim}. '
                         'Use a different index name or matching EMBED_DIM.')
    if str(info.metric).lower() != 'cosine':
        raise ValueError(f'Pinecone index {name} must use cosine metric, got {info.metric}')
    deadline = time.monotonic() + s.pinecone_ready_timeout
    while not pc.describe_index(name).status['ready']:
        if time.monotonic() >= deadline:
            raise TimeoutError(f'Pinecone index {name} was not ready within {s.pinecone_ready_timeout}s')
        time.sleep(3)
    log.info('Pinecone index %s ready', name)
    return pc.Index(name)
# above code state that the index is ready for use and returns the initialized Pinecone index object.



#tenant is someone who is using the system, like a company or organization. 
#namespace is refer to the tenant name in the database, like table. 
#Each tenant will have its own namespace in the Pinecone index.

def namespace(tenant):
    return 'tenant-' + tenant


#pinecone index is a vector database that stores and retrieves high-dimensional vectors.
#upsert and search functions are used to insert and retrieve vectors from the Pinecone index.
#tenant is the namespace, records is a list of vectors to insert, vector is the query vector, 
# groups and owner are used for access control, and limit is the number of results to return.



@trace("upsert")
def upsert(tenant, records):
    return index().upsert(vectors=records, namespace=namespace(tenant))


@trace("search")
def search(tenant, vector, groups, owner, limit=5):
    acl = {'$or': [{'allowed_groups': {'$in': list(groups)}},
                   {'owner_sub': {'$eq': owner}}]}
    result = index().query(vector=vector, top_k=limit, include_metadata=True,
                           namespace=namespace(tenant), filter=acl)
    return [{'id': m.id, 'score': m.score, 'metadata': m.metadata or {}}
            for m in result.matches]
#acl filter is used to ensure that only documents that the user has access to are returned in the search results.

def delete_document(tenant, ids):
    if ids:
        index().delete(ids=ids, namespace=namespace(tenant))