"""中文电商售后领域的工具(7 个)。

工具只做最基本的检查(订单存在、已签收、商品属于订单、支付方式属于顾客);
七日期限、不适用商品、商品是否完好、运费扣除、超额转人工这些政策规则,由 Agent 负责遵守,
评测按数据库终态判断它有没有做对。
"""
import uuid
from typing import List

from tau2.domains.zh_aftersales.data_model import Logistics, Order, Refund, User, ZhAftersalesDB
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool


class ZhAftersalesTools(ToolKitBase):
    """中文电商售后工具集。"""

    db: ZhAftersalesDB

    def __init__(self, db: ZhAftersalesDB) -> None:
        super().__init__(db)

    def _order(self, order_id: str) -> Order:
        if order_id not in self.db.orders:
            raise ValueError("订单不存在")
        return self.db.orders[order_id]

    @is_tool(ToolType.READ)
    def find_user_by_phone(self, phone: str, name: str) -> str:
        """用手机号和姓名核验顾客身份,返回 user_id。两者都对上才算核验通过。

        Args:
            phone: 顾客的手机号,11 位数字,如 '13800138000'。
            name: 顾客姓名,如 '张伟'。

        Returns:
            顾客的 user_id。
        """
        for u in self.db.users.values():
            if u.phone == phone.strip() and u.name == name.strip():
                return u.user_id
        raise ValueError("未找到匹配的顾客,请核对手机号和姓名")

    @is_tool(ToolType.READ)
    def get_user_details(self, user_id: str) -> User:
        """查询顾客资料:姓名、手机号、支付方式、订单列表。

        Args:
            user_id: 顾客 id,如 'u_zhangwei_01'。

        Returns:
            顾客资料。
        """
        if user_id not in self.db.users:
            raise ValueError("顾客不存在")
        return self.db.users[user_id]

    @is_tool(ToolType.READ)
    def get_order_details(self, order_id: str) -> Order:
        """查询订单详情:状态、商品(类别、单价、数量、商品状态、是否赠品)、包邮信息、支付方式、备注、退款记录。

        Args:
            order_id: 订单号,如 'ZH1001'。

        Returns:
            订单详情。
        """
        return self._order(order_id)

    @is_tool(ToolType.READ)
    def get_logistics(self, order_id: str) -> Logistics:
        """查询订单的物流信息,包括签收日期。

        Args:
            order_id: 订单号,如 'ZH1001'。

        Returns:
            物流信息。
        """
        if order_id not in self.db.logistics:
            raise ValueError("没有这个订单的物流信息")
        return self.db.logistics[order_id]

    @is_tool(ToolType.READ)
    def get_policy_article(self, article_no: int) -> str:
        """查询《网络购买商品七日无理由退货暂行办法》某一条的内容。

        Args:
            article_no: 条号,如 10。

        Returns:
            该条的内容。
        """
        key = str(article_no)
        if key not in self.db.articles:
            raise ValueError(f"没有收录第 {article_no} 条,收录的条号:{', '.join(sorted(self.db.articles, key=int))}")
        return self.db.articles[key]

    @is_tool(ToolType.WRITE)
    def apply_return_refund(
        self,
        order_id: str,
        item_ids: List[str],
        refund_method: str,
        deduct_shipping: bool,
        unreturned_gift_ids: List[str],
    ) -> Order:
        """为已签收订单提交七日无理由退货退款申请。执行前必须向顾客列出退货商品、退款金额、扣减项和退款去向,
        并得到顾客明确同意。

        Args:
            order_id: 订单号,如 'ZH1001'。
            item_ids: 要退的商品行 id 列表(不含赠品)。
            refund_method: 退款去向,顾客的支付方式 id,原则上原路退回。
            deduct_shipping: 退货后订单不再满足包邮条件时为 true,退款时扣除包邮免掉的运费。
            unreturned_gift_ids: 顾客无法退回的赠品行 id 列表,按赠品标明的价格从退款中扣除;都退回就传 []。

        Returns:
            提交后的订单详情(含退款记录)。
        """
        order = self._order(order_id)
        if order.status != "delivered":
            raise ValueError("只有已签收的订单可以申请七日无理由退货")
        if not item_ids:
            raise ValueError("至少要退一件商品")
        by_id = {i.item_id: i for i in order.items}
        for iid in item_ids:
            if iid not in by_id or by_id[iid].is_gift:
                raise ValueError(f"{iid} 不是这个订单里可退的商品")
        for gid in unreturned_gift_ids:
            if gid not in by_id or not by_id[gid].is_gift:
                raise ValueError(f"{gid} 不是这个订单的赠品")
        user = self.db.users[order.user_id]
        if refund_method not in user.payment_methods:
            raise ValueError("退款去向必须是顾客自己的支付方式")
        goods = round(sum(by_id[i].price * by_id[i].quantity for i in item_ids), 2)
        ship = order.waived_shipping_fee if deduct_shipping else 0.0
        gifts = round(sum(by_id[g].price * by_id[g].quantity for g in unreturned_gift_ids), 2)
        refund = Refund(refund_id=f"RF{order_id}-{len(order.refunds) + 1}", item_ids=sorted(item_ids),
                        refund_method=refund_method, goods_amount=goods, deducted_shipping=ship,
                        deducted_gifts=gifts, amount=round(goods - ship - gifts, 2), status="退款处理中")
        order.refunds.append(refund)
        order.status = "return_requested"
        return order

    @is_tool(ToolType.GENERIC)
    def transfer_to_human_agents(self, summary: str) -> str:
        """转人工客服,附上问题摘要。只有顾客明确要求、或按政策和现有工具无法处理时才转。

        Args:
            summary: 问题摘要。

        Returns:
            转接结果。
        """
        return "已转接人工客服"
