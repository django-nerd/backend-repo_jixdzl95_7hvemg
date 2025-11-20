import json
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_root():
    r = client.get('/')
    assert r.status_code == 200
    assert 'GPA' in r.json()['message']


def test_gpa_basic():
    payload = {
        "courses": [
            {"code":"T1","name":"Test 1","credit_hours":3,"grade":"A"},
            {"code":"T2","name":"Test 2","credit_hours":3,"grade":"B"}
        ]
    }
    r = client.post('/api/gpa', json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data['gpa'] == 3.5
    assert data['total_credits'] == 6


def test_duplicate_course_validation():
    payload = {
        "courses": [
            {"code":"DUP","name":"X","credit_hours":3,"grade":"A"},
            {"code":"DUP","name":"Y","credit_hours":3,"grade":"B"}
        ]
    }
    r = client.post('/api/gpa', json=payload)
    assert r.status_code == 400


def test_cgpa_and_classification():
    payload = {
        "semesters": [
            {"term":"S1","courses":[{"code":"A","name":"A","credit_hours":3,"grade":"A"}]},
            {"term":"S2","courses":[{"code":"B","name":"B","credit_hours":3,"grade":"B"}]}
        ]
    }
    r = client.post('/api/cgpa', json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data['cgpa'] == 3.5
    assert data['classification'] in {"First Class Honors","Second Class Upper","Second Class Lower","Pass","Fail"}


def test_projection():
    payload = {
        "completed": [{"code":"A","name":"A","credit_hours":3,"grade":"A"}],
        "remaining_credits": 9,
        "target_class": "Second Class Upper"
    }
    r = client.post('/api/project', json=payload)
    assert r.status_code == 200
    data = r.json()
    assert 'needed_avg_gpa' in data


def test_advice():
    payload = {
        "profile": {"user_id":"u1","name":"User","program":"SE","target_class":"Second Class Upper"},
        "semesters": [
            {"term":"S1","courses":[{"code":"M","name":"Math","credit_hours":3,"grade":"C","category":"math"}]},
            {"term":"S2","courses":[{"code":"P","name":"Prog","credit_hours":3,"grade":"B","category":"programming"}]}
        ]
    }
    r = client.post('/api/advice', json=payload)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data['insights'], list)
    assert isinstance(data['recommendations'], list)
