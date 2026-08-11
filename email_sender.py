from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls

    def send_html_email(
        self,
        sender: str,
        recipients: list[str],
        subject: str,
        html_body: str,
    ) -> None:
        if not sender:
            raise ValueError("EMAIL_FROM is empty.")
        if not recipients:
            raise ValueError("No recipient email address was provided.")

        message = MIMEMultipart("alternative")
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(self.host, self.port, timeout=60) as server:
            server.ehlo()
            if self.use_tls:
                server.starttls()
                server.ehlo()

            if self.username:
                server.login(self.username, self.password)

            server.sendmail(sender, recipients, message.as_string())
