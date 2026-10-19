"""全部数据表模型。导入本包即可让 Base.metadata 注册所有表。"""

from app.models.ai_report import AiReport
from app.models.base import Base
from app.models.notice import Notice
from app.models.notification import Notification
from app.models.order import Order
from app.models.order_daily_seq import OrderDailySeq
from app.models.order_file_change import OrderFileChange
from app.models.order_file_version import OrderFileVersion
from app.models.payment_log import PaymentLog
from app.models.print_log import PrintLog
from app.models.queue_session import QueueSession
from app.models.user import User

__all__ = [
    "AiReport",
    "Base",
    "Notice",
    "Notification",
    "Order",
    "OrderDailySeq",
    "OrderFileChange",
    "OrderFileVersion",
    "PaymentLog",
    "PrintLog",
    "QueueSession",
    "User",
]
