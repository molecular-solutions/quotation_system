import email
import socketserver
import threading

import pytest

from app import config
from app.services import mail
from tests.test_api import needs_soffice, payload, seed


class _Smtp(socketserver.StreamRequestHandler):
    """Just enough SMTP to receive one message (no TLS, no auth)."""
    def handle(self):
        w = lambda s: self.wfile.write(s.encode() + b"\r\n")
        w("220 test")
        data, msg = False, []
        for raw in self.rfile:
            line = raw.decode(errors="replace").rstrip("\r\n")
            if data:
                if line == ".":
                    data = False
                    self.server.messages.append("\n".join(msg))
                    w("250 queued")
                else:
                    msg.append(line[1:] if line.startswith("..") else line)
            elif line.upper().startswith("DATA"):
                data = True
                w("354 go")
            elif line.upper().startswith("QUIT"):
                w("221 bye")
                return
            elif line.upper().startswith("EHLO"):
                w("250 hello")
            else:
                w("250 ok")


@pytest.fixture()
def smtp(monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Smtp)
    srv.messages = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k, v in dict(SMTP_HOST="127.0.0.1", SMTP_PORT=srv.server_address[1], SMTP_SECURITY="none",
                     SMTP_USER="", SMTP_FROM="sales@example.com").items():
        monkeypatch.setattr(config, k, v)
    yield srv
    srv.shutdown()


def test_recipient_validation():
    assert mail.clean_recipients([" A@X.in ", "a@x.in", "", "b@y.co"]) == ["a@x.in", "b@y.co"]
    for bad in ("nope", "a@b", "a b@c.d", "a@b.c, d@e.f"):
        with pytest.raises(mail.MailError):
            mail.clean_recipients([bad])


def test_recipients_are_admin_only(authed, make_client):
    r = authed.put("/api/mail/recipients", json={"recipients": ["a@x.in", "b@y.co"]})
    assert r.json() == {"recipients": ["a@x.in", "b@y.co"]}
    assert authed.put("/api/mail/recipients", json={"recipients": ["bad"]}).status_code == 422
    user = make_client()
    assert user.get("/api/mail").json()["recipients"] == ["a@x.in", "b@y.co"]
    assert user.put("/api/mail/recipients", json={"recipients": []}).status_code == 403
    assert user.post("/api/mail/test").status_code == 403


def test_email_needs_setup_and_recipients(authed, monkeypatch):
    ids = seed(authed)
    q = authed.post("/api/quotes", json=payload(ids)).json()
    monkeypatch.setattr(config, "SMTP_HOST", "")
    assert authed.post(f"/api/quotes/{q['id']}/email").status_code == 503
    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_FROM", "sales@example.com")
    assert authed.post(f"/api/quotes/{q['id']}/email").status_code == 422  # no recipients saved


@needs_soffice
def test_email_sends_pdf_to_stored_recipients(authed, smtp):
    ids = seed(authed)
    authed.put("/api/mail/recipients", json={"recipients": ["a@x.in", "b@y.co"]})
    q = authed.post("/api/quotes", json=payload(ids)).json()
    assert q["emailed_at"] is None
    r = authed.post(f"/api/quotes/{q['id']}/email")
    assert r.status_code == 200 and r.json()["sent_to"] == ["a@x.in", "b@y.co"]
    assert authed.get(f"/api/quotes/{q['id']}").json()["emailed_at"]
    msg = email.message_from_string(smtp.messages[0])
    assert msg["To"] == "a@x.in, b@y.co" and q["number"] in msg["Subject"]
    pdf = [p for p in msg.walk() if p.get_content_type() == "application/pdf"][0]
    assert pdf.get_filename().startswith(q["number"]) and pdf.get_payload(decode=True)[:4] == b"%PDF"


def test_email_failure_is_reported_and_not_recorded(authed, monkeypatch):
    ids = seed(authed)
    authed.put("/api/mail/recipients", json={"recipients": ["a@x.in"]})
    q = authed.post("/api/quotes", json=payload(ids)).json()
    for k, v in dict(SMTP_HOST="127.0.0.1", SMTP_PORT=1, SMTP_SECURITY="none", SMTP_USER="", SMTP_FROM="s@x.in").items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr("app.routers.quotes._render_files", lambda q, f: (b"%PDF", "application/pdf", "pdf"))
    r = authed.post(f"/api/quotes/{q['id']}/email")
    assert r.status_code == 502 and "could not send" in r.json()["detail"]["message"]
    assert authed.get(f"/api/quotes/{q['id']}").json()["emailed_at"] is None


def test_users_cannot_email_someone_elses_quote(authed, make_client):
    ids = seed(authed)
    q = authed.post("/api/quotes", json=payload(ids)).json()
    assert make_client().post(f"/api/quotes/{q['id']}/email").status_code == 404
