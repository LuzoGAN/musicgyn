"""Testes de autenticação e cadastro da academia."""
from models import Gym, db
from tests.conftest import login


def test_register_ok(client):
    resp = client.post("/register", data={
        "name": "CrossFit Alfa", "email": "alfa@box.com", "password": "forte123",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Gym.query.filter_by(email="alfa@box.com").first() is not None


def test_register_duplicado(client, gym):
    resp = client.post("/register", data={
        "name": "X", "email": "box@teste.com", "password": "abcdef",
    }, follow_redirects=True)
    assert "já cadastrado" in resp.get_data(as_text=True).lower()


def test_register_senha_curta(client):
    resp = client.post("/register", data={
        "name": "X", "email": "novo@box.com", "password": "123",
    }, follow_redirects=True)
    assert Gym.query.filter_by(email="novo@box.com").first() is None


def test_login_ok(client, gym):
    resp = login(client)
    assert resp.status_code == 200
    assert "Dashboard" in resp.get_data(as_text=True)


def test_login_senha_errada(client, gym):
    resp = client.post("/login", data={"email": "box@teste.com", "password": "errada"},
                       follow_redirects=True)
    assert "inválidos" in resp.get_data(as_text=True).lower()


def test_dashboard_exige_login(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code in (302, 308)
    assert "/login" in resp.headers["Location"]


def test_logout(client, gym):
    login(client)
    resp = client.get("/logout", follow_redirects=True)
    assert resp.status_code == 200
    # após logout, dashboard redireciona p/ login
    resp2 = client.get("/dashboard", follow_redirects=False)
    assert resp2.status_code in (302, 308)
