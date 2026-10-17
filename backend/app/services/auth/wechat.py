"""V2 预留：微信登录。

V2 接入微信登录时，在本文件实现 WechatAuthProvider（用 code 换 openid），
并在 app/services/auth/__init__.py 的工厂里加一个分支、把 AUTH_PROVIDER 改成 wechat，
业务代码不需要改动。
"""

# TODO(V2): class WechatAuthProvider(AuthProvider): name = "wechat"
