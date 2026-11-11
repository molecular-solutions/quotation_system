"""Stand-in for Cloudflare Access: tokens are signed with a key made here, and the app is told to trust its public half.
Kept apart from conftest.py so every test sees the same key (importing conftest as a module would load a second copy)."""
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ISS = "https://team.cloudflareaccess.com"


def access_token(email, aud="test-aud", key=KEY, exp_in=3600, iss=ISS, **claims):
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [aud], "iss": iss, "iat": now, "exp": now + exp_in, **claims}, key, algorithm="RS256")


def login(client, email):
    """Make this client look like it came through Cloudflare Access as `email`. Returns /api/me's response."""
    client.headers["Cf-Access-Jwt-Assertion"] = access_token(email)
    return client.get("/api/me")
