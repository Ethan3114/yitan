"""本地配置。密钥只从环境变量读取，不写进代码。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    invite_code: str = "yitan-local"
    audio_fernet_key: str = ""
    doubao_api_key: str = ""
    doubao_voice: str = "zh_female_vv_jupiter_bigtts"
    doubao_model: str = "1.2.6.1"
    doubao_realtime_url: str = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
    doubao_asr_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    doubao_asr_resource: str = "volc.seedasr.sauc.duration"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_writing_model: str = "deepseek-v4-pro"
    writing_provider: str = "qwen"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_writing_model: str = "qwen3.7-plus"
    alibaba_cloud_access_key_id: str = ""
    alibaba_cloud_access_key_secret: str = ""
    tingwu_app_key: str = ""
    oss_bucket: str = ""
    oss_region: str = "cn-beijing"
    data_dir: str = str(PRODUCT_ROOT / "data")
    database_url: str = ""
    app_port: int = 8011
    web_origin: str = "http://127.0.0.1:3011"
    session_days: int = 30
    deployment_mode: str = "local"
    cookie_secure: bool = False
    auth_pepper: str = ""
    cloudbase_env_id: str = ""
    cloudbase_api_key: str = ""
    proxy_shared_secret: str = ""
    worker_token: str = ""
    worker_url: str = ""

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return "sqlite:///" + str(self.data_path / "yitan.db")

    @property
    def voice_ready(self) -> bool:
        return bool(self.doubao_api_key.strip())

    @property
    def writing_ready(self) -> bool:
        return bool(self.writing_key.strip())

    @property
    def writing_model(self) -> str:
        return self.qwen_writing_model if self.writing_provider == "qwen" else self.deepseek_writing_model

    @property
    def writing_key(self) -> str:
        if self.writing_provider not in {"qwen", "deepseek"}:
            raise ValueError("Unsupported writing provider")
        return self.dashscope_api_key if self.writing_provider == "qwen" else self.deepseek_api_key

    @property
    def writing_url(self) -> str:
        return self.dashscope_base_url if self.writing_provider == "qwen" else self.deepseek_base_url

    @property
    def tingwu_ready(self) -> bool:
        return bool(
            self.alibaba_cloud_access_key_id.strip()
            and self.alibaba_cloud_access_key_secret.strip()
            and self.tingwu_app_key.strip()
            and self.oss_bucket.strip()
        )


def get_settings() -> Settings:
    return Settings()
