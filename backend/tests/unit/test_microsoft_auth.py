"""Validate actual locally signed tokens without provider/database connections."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jwt.exceptions import PyJWKClientConnectionError

from app.api.v1.endpoints import auth
from app.core import microsoft_auth as microsoft
from app.core.database import get_db

CLIENT = '11111111-1111-4111-8111-111111111111'
TENANT = '22222222-2222-4222-8222-222222222222'
OID = '33333333-3333-4333-8333-333333333333'

@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(auth.settings, 'MICROSOFT_CLIENT_ID', CLIENT)
    monkeypatch.setattr(auth.settings, 'MICROSOFT_TENANT_ID', TENANT)
    monkeypatch.setattr(auth.settings, 'MICROSOFT_ACCOUNT_BINDINGS', {OID: 'employee@hrgroup3.com'})
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = Mock()
    jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=key.public_key())
    monkeypatch.setattr(microsoft, '_jwks_client', Mock(return_value=jwks))
    now = datetime.now(timezone.utc)
    claims = dict(iss=f'https://login.microsoftonline.com/{TENANT}/v2.0', aud=CLIENT,
                  tid=TENANT, oid=OID, sub='subject', ver='2.0',
                  iat=now, nbf=now, exp=now + timedelta(minutes=5))
    def token(**changes):
        return jwt.encode({**claims, **changes}, key, algorithm='RS256', headers={'kid': 'local'})
    return token, jwks

async def request(token, active=True):
    db = AsyncMock()
    user = None if active is None else SimpleNamespace(user_account_id=7, is_active=active)
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: user)
    app = FastAPI()
    app.include_router(auth.router, prefix='/auth')
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/auth/login/microsoft', json={'id_token': token})
    return response, db

@pytest.mark.parametrize('active,expected', [(True, 200), (False, 403), (None, 403)])
async def test_existing_active_bound_account_only(identity, active, expected):
    token, _ = identity
    response, db = await request(token(), active)
    assert response.status_code == expected
    if expected == 200:
        assert response.json()['access_token'] and response.json()['refresh_token']
        db.commit.assert_awaited_once()
    else:
        db.commit.assert_not_awaited()

@pytest.mark.parametrize('changes', [
    {'aud': 'wrong-client'}, {'iss': 'https://attacker.test'}, {'tid': CLIENT},
    {'exp': 1}, {'nbf': 9999999999}, {'ver': '1.0'}, {'oid': 'invalid'}, {'sub': None},
])
async def test_claim_validation_before_database(identity, changes):
    token, _ = identity
    response, db = await request(token(**changes))
    assert response.status_code == 401
    db.execute.assert_not_awaited()

async def test_unbound_identity_cannot_take_over_account_by_email(identity):
    token, _ = identity
    response, db = await request(token(oid=CLIENT, email='employee@hrgroup3.com', preferred_username='employee@hrgroup3.com'))
    assert response.status_code == 403
    db.execute.assert_not_awaited()

@pytest.mark.parametrize('bad_token', ['not-a-jwt', jwt.encode({'sub': 'test'}, 'test-key-with-at-least-32-characters', algorithm='HS256')])
async def test_malformed_or_wrong_algorithm_does_not_fetch_keys(identity, bad_token):
    response, db = await request(bad_token)
    assert response.status_code == 401
    microsoft._jwks_client.assert_not_called()
    db.execute.assert_not_awaited()

async def test_bad_signature(identity):
    token, jwks = identity
    jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(
        key=rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key())
    response, db = await request(token())
    assert response.status_code == 401
    db.execute.assert_not_awaited()

async def test_provider_outage_is_retriable(identity):
    token, jwks = identity
    jwks.get_signing_key_from_jwt.side_effect = PyJWKClientConnectionError('offline')
    response, db = await request(token())
    assert response.status_code == 503
    db.execute.assert_not_awaited()

async def test_public_config_and_disabled_login(identity, monkeypatch):
    token, _ = identity
    app = FastAPI()
    app.include_router(auth.router, prefix='/auth')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/auth/microsoft/config')).json() == {
            'enabled': True, 'client_id': CLIENT, 'tenant_id': TENANT}
        monkeypatch.setattr(auth.settings, 'MICROSOFT_TENANT_ID', 'common')
        assert (await client.get('/auth/microsoft/config')).json() == {'enabled': False}
    response, db = await request(token())
    assert response.status_code == 503
    db.execute.assert_not_awaited()
