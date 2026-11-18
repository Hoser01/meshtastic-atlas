import sqlite3
from unittest.mock import patch
from atlas_server.api import create_app
from fastapi.testclient import TestClient

def test_liveness_does_not_query_store(tmp_path):
    app=create_app(tmp_path/'atlas.db')
    with TestClient(app) as client, patch.object(app.state.store,'stats',side_effect=AssertionError('heavy stats called')):
        response=client.get('/api/v1/healthz')
        assert response.status_code==200
        assert response.json()=={'status':'ok','check':'liveness','dependencies_checked':False}
        assert response.headers['cache-control']=='no-store'

def test_readiness_uses_bounded_read_only_metadata_query(tmp_path):
    app=create_app(tmp_path/'atlas.db')
    original=sqlite3.connect
    calls=[]
    def connect(*args,**kwargs):
        calls.append((args,kwargs));return original(*args,**kwargs)
    with TestClient(app) as client, patch('atlas_server.api.sqlite3.connect',side_effect=connect), patch.object(app.state.store,'stats',side_effect=AssertionError('heavy stats called')):
        response=client.get('/api/v1/readyz')
        assert response.status_code==200
        assert response.json()['database']=='readable'
        assert response.headers['cache-control']=='no-store'
    assert len(calls)==1
    assert calls[0][0][0].endswith('?mode=ro')
    assert calls[0][1]=={'uri':True,'timeout':0.05}

def test_database_failure_is_503_without_internal_details(tmp_path):
    app=create_app(tmp_path/'atlas.db')
    with TestClient(app) as client, patch('atlas_server.api.sqlite3.connect',side_effect=sqlite3.OperationalError('private path')):
        response=client.get('/api/v1/readyz')
        assert response.status_code==503
        assert response.json()['database']=='unavailable'
        assert 'private' not in response.text
