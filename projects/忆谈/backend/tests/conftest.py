"""测试夹具：临时库，不连真实语音。"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path):
    key = Fernet.generate_key().decode("utf-8")
    settings = Settings(
        invite_code="yitan-local",
        audio_fernet_key=key,
        doubao_api_key="",
        deepseek_api_key="",
        dashscope_api_key="",
        alibaba_cloud_access_key_id="",
        alibaba_cloud_access_key_secret="",
        tingwu_app_key="",
        oss_bucket="",
        data_dir=str(tmp_path),
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        web_origin="http://127.0.0.1:3011",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
