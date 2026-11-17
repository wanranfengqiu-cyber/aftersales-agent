"""中文电商售后领域(七日无理由退货)的数据模型。"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from tau2.domains.zh_aftersales.utils import ZH_DB_PATH
from tau2.environment.db import DB


class Item(BaseModel):
    item_id: str = Field(description="商品行 id")
    name: str = Field(description="商品名称")
    category: str = Field(description="商品类别:普通商品 / 定制商品 / 生鲜 / 数字商品 / 报刊")
    price: float = Field(description="单价(赠品为事先标明的价格,不计入实付)")
    quantity: int = Field(description="数量")
    condition: str = Field(description="商品状态:完好 / 拆封查验 / 已使用 / 吊牌已剪 / 密封包装已拆")
    is_gift: bool = Field(default=False, description="是否赠品")


class Refund(BaseModel):
    refund_id: str
    item_ids: List[str]
    refund_method: str = Field(description="退款去向(支付方式 id)")
    goods_amount: float = Field(description="退回商品的价款")
    deducted_shipping: float = Field(description="因不再满足包邮条件扣除的运费")
    deducted_gifts: float = Field(description="未退回赠品按标明价格扣除的金额")
    amount: float = Field(description="实际退款金额")
    status: str = Field(description="退款状态")


class Order(BaseModel):
    order_id: str
    user_id: str
    status: str = Field(description="pending 待发货 / shipped 已发货 / delivered 已签收 / return_requested 退货退款中")
    items: List[Item]
    goods_amount: float = Field(description="商品总价(不含赠品)")
    free_shipping: bool = Field(description="下单时是否因满额包邮")
    free_shipping_threshold: float = Field(default=99.0, description="包邮门槛")
    waived_shipping_fee: float = Field(default=10.0, description="包邮时免掉的运费")
    payment_method: str = Field(description="下单时的支付方式 id")
    note: str = Field(default="", description="订单备注")
    refunds: List[Refund] = Field(default_factory=list)


class Logistics(BaseModel):
    order_id: str
    carrier: str
    tracking_no: str
    delivered_at: Optional[str] = Field(description="签收日期 YYYY-MM-DD,未签收为 null")
    events: List[str] = Field(default_factory=list)
    remark: str = Field(default="", description="物流备注")


class User(BaseModel):
    user_id: str
    name: str
    phone: str
    payment_methods: Dict[str, str] = Field(description="支付方式 id -> 说明")
    orders: List[str]


class ZhAftersalesDB(DB):
    users: Dict[str, User]
    orders: Dict[str, Order]
    logistics: Dict[str, Logistics]
    articles: Dict[str, str] = Field(description="法规条款,键为条号,如 '10'")

    def get_statistics(self) -> dict:
        return {"num_users": len(self.users), "num_orders": len(self.orders)}


def get_db():
    return ZhAftersalesDB.load(ZH_DB_PATH)
