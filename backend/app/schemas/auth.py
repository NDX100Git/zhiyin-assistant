"""登录相关的出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """V1 设备登录：device_id 与 code 至少提供一个（code 为微信小程序 code 预留）。"""

    device_id: str | None = Field(default=None, description="设备标识（APP 端）")
    code: str | None = Field(default=None, description="微信小程序登录 code（V1 可选）")
    nickname: str | None = Field(default=None, description="昵称，非必填真实姓名")


class UserOut(BaseModel):
    id: int
    nickname: str | None = None
    auth_provider: str


class LoginData(BaseModel):
    token: str
    expires_in: int = Field(description="token 有效期，单位秒")
    user: UserOut


class ProfileRequest(BaseModel):
    nickname: str = Field(description="昵称，非必填真实姓名，最多 64 字")
