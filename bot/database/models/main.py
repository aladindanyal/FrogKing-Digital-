import datetime
from typing import Any

from sqlalchemy import (
    Column, Integer, String, BigInteger, ForeignKey, Text, Boolean,
    DateTime, Numeric, Index, UniqueConstraint, CheckConstraint, func, select, JSON, text
)
from sqlalchemy.dialects.postgresql import JSONB
from bot.database.main import Database
from sqlalchemy.orm import relationship, backref


class Permission:
    USE             = 1 << 0   #   1 — basic access
    BROADCAST       = 1 << 1   #   2 — mass messaging
    SETTINGS_MANAGE = 1 << 2   #   4 — bot settings (maintenance, etc.)
    USERS_MANAGE    = 1 << 3   #   8 — view/block/unblock users, referrals, purchases
    CATALOG_MANAGE  = 1 << 4   #  16 — categories, positions, items/goods CRUD
    ADMINS_MANAGE   = 1 << 5   #  32 — role CRUD, role assignment
    OWN             = 1 << 6   #  64 — owner-only operations
    STATS_VIEW      = 1 << 7   # 128 — statistics, logs, bought-item search
    BALANCE_MANAGE  = 1 << 8   # 256 — top-up / deduct user balance
    PROMO_MANAGE    = 1 << 9   # 512 — promo code CRUD

    @staticmethod
    def is_subset(perms: int, of: int) -> bool:
        """True if every bit in `perms` is also set in `of`."""
        return (perms & ~of) == 0

    @staticmethod
    def has_any_admin_perm(perms: int) -> bool:
        """True if `perms` has any permission beyond USE."""
        return (perms & ~Permission.USE) != 0


class Role(Database.BASE):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True)
    default = Column(Boolean, default=False, index=True)
    permissions = Column(Integer)
    users = relationship('User', backref='role', lazy='raise')

    def __init__(self, name: str = None, permissions=None, **kwargs):
        super(Role, self).__init__(**kwargs)
        if name is not None:
            self.name = name
        if permissions is not None:
            self.permissions = permissions
        elif self.permissions is None:
            self.permissions = 0

    def __str__(self):
        return self.name or ""

    @staticmethod
    async def insert_roles():
        roles = {
            'USER': [Permission.USE],
            'ADMIN': [Permission.USE, Permission.BROADCAST,
                      Permission.SETTINGS_MANAGE, Permission.USERS_MANAGE,
                      Permission.CATALOG_MANAGE, Permission.STATS_VIEW,
                      Permission.BALANCE_MANAGE, Permission.PROMO_MANAGE],
            'OWNER': [Permission.USE, Permission.BROADCAST,
                      Permission.SETTINGS_MANAGE, Permission.USERS_MANAGE,
                      Permission.CATALOG_MANAGE, Permission.ADMINS_MANAGE,
                      Permission.OWN, Permission.STATS_VIEW,
                      Permission.BALANCE_MANAGE, Permission.PROMO_MANAGE],
        }
        default_role = 'USER'
        async with Database().session() as s:
            for r, perms in roles.items():
                result = await s.execute(select(Role).filter_by(name=r))
                role = result.scalars().first()
                if role is None:
                    role = Role(name=r)
                    s.add(role)
                role.reset_permissions()
                for perm in perms:
                    role.add_permission(perm)
                role.default = (role.name == default_role)

    def add_permission(self, perm):
        self.permissions |= perm

    def remove_permission(self, perm):
        self.permissions &= ~perm

    def reset_permissions(self):
        self.permissions = 0

    def has_permission(self, perm):
        return self.permissions & perm == perm

    def __repr__(self):
        return '<Role %r>' % self.name


class User(Database.BASE):
    __tablename__ = 'users'
    telegram_id = Column(BigInteger, primary_key=True)
    telegram_username = Column(String(64), nullable=True, index=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    profile_updated_at = Column(DateTime(timezone=True), nullable=True)
    role_id = Column(Integer, ForeignKey('roles.id', ondelete="RESTRICT"), default=1, index=True)
    balance = Column(Numeric(12, 2), nullable=False, default=0)
    referral_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    registration_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_blocked = Column(Boolean, default=False, index=True)
    user_operations = relationship("Operations", back_populates="user_telegram_id", lazy='raise')
    user_goods = relationship("BoughtGoods", back_populates="user_telegram_id", lazy='raise')

    __table_args__ = (
        CheckConstraint('referral_id != telegram_id', name='ck_users_no_self_referral'),
        Index('ix_users_registration_date', 'registration_date'),
    )

    referral_earnings_received = relationship(
        "ReferralEarnings",
        foreign_keys="ReferralEarnings.referrer_id",
        back_populates="referrer",
        lazy='raise',
    )
    referral_earnings_generated = relationship(
        "ReferralEarnings",
        foreign_keys="ReferralEarnings.referral_id",
        back_populates="referral",
        lazy='raise',
    )

    def __init__(self, telegram_id: int = None, registration_date: datetime.datetime = None, balance=None,
                 referral_id=None, role_id: int = None, **kw: Any):
        super().__init__(**kw)
        if telegram_id is not None:
            self.telegram_id = telegram_id
        if role_id is not None:
            self.role_id = role_id
        if balance is not None:
            self.balance = balance
        if referral_id is not None:
            self.referral_id = referral_id
        if registration_date is not None:
            self.registration_date = registration_date

    def __str__(self):
        return str(self.telegram_id)


class StoreSettings(Database.BASE):
    __tablename__ = 'store_settings'
    id = Column(Integer, primary_key=True)
    shop_root_title = Column(String(255), nullable=True)
    shop_root_description = Column(Text, nullable=True)
    main_menu_title = Column(String(255), nullable=True)
    main_menu_description = Column(Text, nullable=True)
    main_menu_image_path = Column(String(500), nullable=True)
    main_menu_image_url = Column(String(500), nullable=True)
    main_menu_footer = Column(String(255), nullable=True)
    root_category_columns = Column(Integer, nullable=False, default=1, server_default="1")
    subcategory_columns = Column(Integer, nullable=False, default=2, server_default="2")
    product_columns = Column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint('root_category_columns IN (1, 2)', name='ck_store_settings_root_cols'),
        CheckConstraint('subcategory_columns IN (1, 2)', name='ck_store_settings_subcat_cols'),
        CheckConstraint('product_columns IN (1, 2)', name='ck_store_settings_product_cols'),
    )


class MainMenuButtonSettings(Database.BASE):
    __tablename__ = 'main_menu_button_settings'
    id = Column(Integer, primary_key=True)
    action_key = Column(String(50), unique=True, nullable=False, index=True)
    label_en = Column(String(255), nullable=True)
    label_ar = Column(String(255), nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True, server_default='true')
    row_order = Column(Integer, nullable=False, default=0, server_default='0')
    column_order = Column(Integer, nullable=False, default=0, server_default='0')
    owner_only = Column(Boolean, nullable=False, default=False, server_default='false')


class Categories(Database.BASE):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey('categories.id', ondelete="SET NULL"), nullable=True, index=True)

    items = relationship("Goods", back_populates="category", lazy='raise')
    parent = relationship("Categories", remote_side=[id], back_populates="subcategories")
    subcategories = relationship("Categories", back_populates="parent", lazy='raise')

    def __init__(self, name: str = None, **kw: Any):
        super().__init__(**kw)
        if name is not None:
            self.name = name

    def __str__(self):
        return self.name or ""


class Goods(Database.BASE):
    __tablename__ = 'goods'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    description = Column(Text, nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id', ondelete="CASCADE"), nullable=False, index=True)
    fulfillment_mode = Column(String(20), nullable=False, default="instant", server_default="instant")
    fulfillment_eta_minutes = Column(Integer, nullable=True)
    manual_instructions_i18n = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=True)
    customer_input_intro_i18n = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=True)

    __table_args__ = (
        CheckConstraint("fulfillment_mode IN ('instant', 'manual')", name='ck_goods_fulfillment_mode'),
        CheckConstraint('fulfillment_eta_minutes IS NULL OR fulfillment_eta_minutes > 0', name='ck_goods_fulfillment_eta_positive'),
    )

    category = relationship("Categories", back_populates="items", lazy='raise')
    values = relationship("ItemValues", back_populates="item", cascade="all, delete-orphan", passive_deletes=True, lazy='raise')
    customer_fields = relationship("ProductCustomerField", back_populates="goods", cascade="all, delete-orphan", passive_deletes=True, lazy='raise')

    def __init__(self, name: str = None, price=None, description: str = None, category_id: int = None, **kw: Any):
        super().__init__(**kw)
        if name is not None:
            self.name = name
        if price is not None:
            self.price = price
        if description is not None:
            self.description = description
        if category_id is not None:
            self.category_id = category_id

    def __str__(self):
        return self.name or ""


class ProductCustomerField(Database.BASE):
    __tablename__ = 'product_customer_fields'
    id = Column(Integer, primary_key=True)
    goods_id = Column(Integer, ForeignKey('goods.id', ondelete="CASCADE"), nullable=False, index=True)
    field_key = Column(String(64), nullable=False)
    field_type = Column(String(20), nullable=False)
    label_i18n = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=False)
    placeholder_i18n = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=True)
    help_text_i18n = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=True)
    required = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    is_sensitive = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    scope = Column(String(20), default="per_order", server_default="per_order", nullable=False)
    sort_order = Column(Integer, default=0, server_default=text("0"), nullable=False)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    min_length = Column(Integer, nullable=True)
    max_length = Column(Integer, nullable=True)
    select_options_i18n = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('goods_id', 'field_key', name='uq_product_customer_field_key'),
        CheckConstraint("field_type IN ('text', 'textarea', 'email', 'phone', 'username', 'url', 'select', 'secret')", name='ck_prod_cust_field_type'),
        CheckConstraint("scope IN ('per_order', 'per_unit')", name='ck_prod_cust_field_scope'),
        CheckConstraint('sort_order >= 0', name='ck_prod_cust_field_sort_pos'),
        CheckConstraint('min_length IS NULL OR min_length >= 0', name='ck_prod_cust_field_min_len'),
        CheckConstraint('max_length IS NULL OR max_length > 0', name='ck_prod_cust_field_max_len'),
        Index('ix_prod_cust_field_goods_active_sort', 'goods_id', 'is_active', 'sort_order'),
    )

    goods = relationship("Goods", back_populates="customer_fields", lazy='raise')

    def __str__(self):
        return f"{self.goods_id}:{self.field_key}"


class ItemValues(Database.BASE):
    __tablename__ = 'item_values'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('goods.id', ondelete="CASCADE"), nullable=False, index=True)
    value = Column(Text, nullable=True)
    is_infinity = Column(Boolean, nullable=False)
    item = relationship("Goods", back_populates="values", lazy='raise')

    __table_args__ = (
        UniqueConstraint('item_id', 'value', name='uq_item_value_per_item'),
        Index('ix_item_values_item_inf', 'item_id', 'is_infinity'),
    )

    def __init__(self, item_id: int = None, value: str = None, is_infinity: bool = None, **kw: Any):
        super().__init__(**kw)
        if item_id is not None:
            self.item_id = item_id
        if value is not None:
            self.value = value
        if is_infinity is not None:
            self.is_infinity = is_infinity

    def __str__(self):
        return f"#{self.id} ({self.item_id})"

class Order(Database.BASE):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    public_id = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending")
    currency = Column(String(8), nullable=False, default="USD")
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    discount_total = Column(Numeric(12, 2), nullable=False, default=0)
    total = Column(Numeric(12, 2), nullable=False, default=0)
    promo_code_snapshot = Column(String(50), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    failure_code = Column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'paid', 'processing', 'completed', 'cancelled', 'failed', 'refunded')", name='ck_order_status'),
        CheckConstraint('subtotal >= 0', name='ck_order_subtotal_pos'),
        CheckConstraint('discount_total >= 0', name='ck_order_discount_pos'),
        CheckConstraint('total >= 0', name='ck_order_total_pos'),
        CheckConstraint('discount_total <= subtotal', name='ck_order_discount_max'),
        Index('ix_order_user_created', 'user_id', 'created_at'),
        Index('ix_order_user_status', 'user_id', 'status'),
        Index('ix_order_status_created', 'status', 'created_at'),
    )

    user = relationship("User", backref="orders", lazy='raise')
    items = relationship("OrderItem", back_populates="order", lazy='raise', cascade="all, delete-orphan")

    def __str__(self):
        return self.public_id or ""


class OrderItem(Database.BASE):
    __tablename__ = 'order_items'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id', ondelete="CASCADE"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey('goods.id', ondelete="SET NULL"), nullable=True, index=True)

    product_name_snapshot = Column(String(255), nullable=False)
    product_description_snapshot = Column(Text, nullable=True)
    quantity = Column(Integer, nullable=False, default=1)

    unit_price = Column(Numeric(12, 2), nullable=False)
    subtotal = Column(Numeric(12, 2), nullable=False)
    discount_total = Column(Numeric(12, 2), nullable=False, default=0)
    total = Column(Numeric(12, 2), nullable=False)

    fulfillment_status = Column(String(20), nullable=False, default="pending")

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint('quantity > 0', name='ck_order_item_qty_positive'),
        CheckConstraint("fulfillment_status IN ('pending', 'processing', 'delivered', 'failed', 'cancelled', 'refunded')", name='ck_order_item_fstatus'),
        CheckConstraint('subtotal >= 0', name='ck_order_item_subtotal_pos'),
        CheckConstraint('discount_total >= 0', name='ck_order_item_discount_pos'),
        CheckConstraint('total >= 0', name='ck_order_item_total_pos'),
        CheckConstraint('discount_total <= subtotal', name='ck_order_item_discount_max'),
        Index('ix_order_item_order_fstatus', 'order_id', 'fulfillment_status'),
        Index('ix_order_item_fstatus', 'fulfillment_status'),
    )

    order = relationship("Order", back_populates="items", lazy='raise')
    item = relationship("Goods", backref="order_items", lazy='raise')
    bought_goods = relationship("BoughtGoods", back_populates="order_item", lazy='raise')

    def __str__(self):
        return f"{self.product_name_snapshot} x{self.quantity}"


class BoughtGoods(Database.BASE):
    __tablename__ = 'bought_goods'
    id = Column(Integer, primary_key=True)
    item_name = Column(String(100), nullable=False, index=True)
    value = Column(Text, nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    buyer_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    bought_datetime = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    unique_id = Column(BigInteger, nullable=False, unique=True)

    order_id = Column(Integer, ForeignKey('orders.id', ondelete="SET NULL"), nullable=True, index=True)
    order_item_id = Column(Integer, ForeignKey('order_items.id', ondelete="SET NULL"), nullable=True, index=True)

    user_telegram_id = relationship("User", back_populates="user_goods", lazy='raise')
    order = relationship("Order", backref="bought_goods", lazy='raise')
    order_item = relationship("OrderItem", back_populates="bought_goods", lazy='raise')

    __table_args__ = (
        Index('ix_bought_goods_datetime', 'bought_datetime'),
        Index('ix_bought_goods_buyer_datetime', 'buyer_id', 'bought_datetime'),
    )

    def __init__(self, name: str = None, value: str = None, price=None, bought_datetime=None,
                 unique_id=None, buyer_id: int = None, order_id: int = None, order_item_id: int = None, **kw: Any):
        super().__init__(**kw)
        if name is not None:
            self.item_name = name
        if value is not None:
            self.value = value
        if price is not None:
            self.price = price
        if buyer_id is not None:
            self.buyer_id = buyer_id
        if bought_datetime is not None:
            self.bought_datetime = bought_datetime
        if unique_id is not None:
            self.unique_id = unique_id
        if order_id is not None:
            self.order_id = order_id
        if order_item_id is not None:
            self.order_item_id = order_item_id

    def __str__(self):
        return self.item_name or ""


class Operations(Database.BASE):
    __tablename__ = 'operations'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    operation_value = Column(Numeric(12, 2), nullable=False)
    operation_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_telegram_id = relationship("User", back_populates="user_operations", lazy='raise')

    __table_args__ = (
        Index('ix_operations_time', 'operation_time'),
    )

    def __init__(self, user_id: int = None, operation_value=None, operation_time=None, **kw: Any):
        super().__init__(**kw)
        if user_id is not None:
            self.user_id = user_id
        if operation_value is not None:
            self.operation_value = operation_value
        if operation_time is not None:
            self.operation_time = operation_time

    def __str__(self):
        return f"#{self.id}"


class Payments(Database.BASE):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False, index=True)
    external_id = Column(String(128), nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(8), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('provider', 'external_id', name='uq_payment_provider_ext'),
        Index('ix_payments_status_created', 'status', 'created_at'),
    )

    def __str__(self):
        return f"{self.provider}:{self.external_id}"


class ReferralEarnings(Database.BASE):
    __tablename__ = 'referral_earnings'

    id = Column(Integer, primary_key=True)
    referrer_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    referral_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    original_amount = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    referrer = relationship(
        "User",
        foreign_keys="ReferralEarnings.referrer_id",
        back_populates="referral_earnings_received",
        lazy='raise',
    )
    referral = relationship(
        "User",
        foreign_keys="ReferralEarnings.referral_id",
        back_populates="referral_earnings_generated",
        lazy='raise',
    )

    __table_args__ = (
        CheckConstraint('referrer_id != referral_id', name='ck_referral_earnings_no_self_referral'),
        Index('ix_referral_earnings_referrer_created', 'referrer_id', 'created_at'),
        Index('ix_referral_earnings_referral_created', 'referral_id', 'created_at'),
    )

    def __init__(self, referrer_id: int = None, referral_id: int = None, amount=None, original_amount=None, **kw: Any):
        super().__init__(**kw)
        if referrer_id is not None:
            self.referrer_id = referrer_id
        if referral_id is not None:
            self.referral_id = referral_id
        if amount is not None:
            self.amount = amount
        if original_amount is not None:
            self.original_amount = original_amount

    def __str__(self):
        return f"#{self.id}"


class AuditLog(Database.BASE):
    __tablename__ = 'audit_log'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    level = Column(String(8), nullable=False, default="INFO")
    user_id = Column(BigInteger, nullable=True)
    action = Column(String(64), nullable=False)
    resource_type = Column(String(32), nullable=True)
    resource_id = Column(String(128), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)

    __table_args__ = (
        Index('ix_audit_log_timestamp', 'timestamp'),
        Index('ix_audit_log_user_id', 'user_id'),
        Index('ix_audit_log_action', 'action'),
    )

    def __repr__(self):
        return f'<AuditLog {self.action} user={self.user_id} @ {self.timestamp}>'

    def __str__(self):
        return self.action or ""


class PromoCodes(Database.BASE):
    __tablename__ = 'promo_codes'
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    discount_type = Column(String(10), nullable=False)  # 'percent' | 'fixed'
    discount_value = Column(Numeric(12, 2), nullable=False)
    max_uses = Column(Integer, nullable=False, default=0)  # 0 = unlimited
    current_uses = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    category_id = Column(Integer, ForeignKey('categories.id', ondelete='SET NULL'), nullable=True)
    item_id = Column(Integer, ForeignKey('goods.id', ondelete='SET NULL'), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __str__(self):
        return self.code or ""


class PromoCodeUsages(Database.BASE):
    __tablename__ = 'promo_code_usages'
    id = Column(Integer, primary_key=True)
    promo_id = Column(Integer, ForeignKey('promo_codes.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint('promo_id', 'user_id', name='uq_promo_usage_per_user'),)


class CartItems(Database.BASE):
    __tablename__ = 'cart_items'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False, index=True)
    item_name = Column(String(100), nullable=False)
    promo_code = Column(String(50), nullable=True)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __str__(self):
        return self.item_name or ""


class Reviews(Database.BASE):
    __tablename__ = 'reviews'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False, index=True)
    item_name = Column(String(100), nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1-5
    text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('user_id', 'item_name', name='uq_review_per_user_item'),
        CheckConstraint('rating >= 1 AND rating <= 5', name='ck_review_rating_range'),
    )

    def __str__(self):
        return f"{self.item_name} ({self.rating}★)"


class ProductRestockSubscription(Database.BASE):
    __tablename__ = 'product_restock_subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey('goods.id', ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="active", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    notified_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'item_id', name='uq_restock_sub_user_item'),
        Index('ix_restock_sub_status', 'status'),
        Index('ix_restock_sub_item_status', 'item_id', 'status'),
    )

    user = relationship("User", backref="restock_subscriptions", lazy='raise')
    item = relationship("Goods", backref=backref("restock_subscriptions", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')

    def __str__(self):
        return f"Sub #{self.id} (User {self.user_id}, Item {self.item_id})"

class CheckoutIntakeDraft(Database.BASE):
    __tablename__ = 'checkout_intake_drafts'
    id = Column(Integer, primary_key=True)
    public_token = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    goods_id = Column(Integer, ForeignKey('goods.id', ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    schema_fingerprint = Column(String(64), nullable=False)
    encrypted_payload = Column(Text, nullable=False)
    encryption_version = Column(Integer, nullable=False)
    current_step = Column(Integer, nullable=False, default=0, server_default=text("0"))

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)

    order_id = Column(Integer, ForeignKey('orders.id', ondelete="SET NULL"), unique=True, nullable=True)

    __table_args__ = (
        CheckConstraint('quantity > 0', name='ck_draft_quantity_pos'),
        CheckConstraint('current_step >= 0', name='ck_draft_current_step_pos'),
        CheckConstraint('encryption_version > 0', name='ck_draft_enc_ver_pos'),
        CheckConstraint("status IN ('pending', 'consumed', 'expired', 'cancelled', 'invalidated')", name='ck_draft_status'),
        CheckConstraint('expires_at > created_at', name='ck_draft_expires_after_created'),
        CheckConstraint("(status != 'consumed') OR (consumed_at IS NOT NULL)", name='ck_draft_consumed_time'),
        CheckConstraint("(status != 'cancelled') OR (cancelled_at IS NOT NULL)", name='ck_draft_cancelled_time'),
        CheckConstraint("(status != 'invalidated') OR (invalidated_at IS NOT NULL)", name='ck_draft_invalidated_time'),
        Index('ix_draft_user_status_updated', 'user_id', 'status', 'updated_at'),
        Index('ix_draft_user_goods_status', 'user_id', 'goods_id', 'status'),
        Index('ix_draft_status_expires', 'status', 'expires_at'),
        Index('ix_draft_goods_status', 'goods_id', 'status'),
    )

    user = relationship("User", backref=backref("checkout_drafts", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    goods = relationship("Goods", backref=backref("checkout_drafts", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    order = relationship("Order", backref="checkout_draft", lazy='raise')

    def __str__(self):
        return f"Draft #{self.id} ({self.status})"


class OrderCustomerInput(Database.BASE):
    __tablename__ = 'order_customer_inputs'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id', ondelete="CASCADE"), nullable=False, index=True)
    order_item_id = Column(Integer, ForeignKey('order_items.id', ondelete="CASCADE"), nullable=False, index=True)
    original_field_id = Column(Integer, ForeignKey('product_customer_fields.id', ondelete="SET NULL"), nullable=True, index=True)

    field_key_snapshot = Column(String(64), nullable=False)
    label_i18n_snapshot = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=False)
    field_type_snapshot = Column(String(20), nullable=False)
    scope_snapshot = Column(String(20), nullable=False)
    unit_index = Column(Integer, nullable=False)

    is_sensitive = Column(Boolean, nullable=False)
    encrypted_value = Column(Text, nullable=False)
    masked_preview = Column(String(255), nullable=False)
    encryption_version = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(scope_snapshot = 'per_order' AND unit_index = 0) OR (scope_snapshot = 'per_unit' AND unit_index > 0)",
            name='ck_oci_scope_unit_index'
        ),
        UniqueConstraint('order_item_id', 'field_key_snapshot', 'unit_index', name='uq_oci_item_key_unit'),
    )

    order = relationship("Order", backref=backref("customer_inputs", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    order_item = relationship("OrderItem", backref=backref("customer_inputs", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')

    def __str__(self):
        return f"Input #{self.id} for OrderItem {self.order_item_id}"


class ManualFulfillmentJob(Database.BASE):
    __tablename__ = 'manual_fulfillment_jobs'
    id = Column(Integer, primary_key=True)
    order_item_id = Column(Integer, ForeignKey('order_items.id', ondelete="CASCADE"), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="queued")
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))

    started_at = Column(DateTime(timezone=True), nullable=True)
    waiting_customer_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    started_by = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True)
    completed_by = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'in_progress', 'waiting_customer', 'completed', 'failed', 'cancelled')", name='ck_mfj_status'),
        CheckConstraint('version > 0', name='ck_mfj_version_pos'),
    )

    order_item = relationship("OrderItem", backref=backref("fulfillment_job", cascade="all, delete-orphan", passive_deletes=True, uselist=False), lazy='raise')

    def __str__(self):
        return f"Job #{self.id} ({self.status})"


class ManualOrderInteraction(Database.BASE):
    __tablename__ = 'manual_order_interactions'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id', ondelete="CASCADE"), nullable=False, index=True)
    fulfillment_job_id = Column(Integer, ForeignKey('manual_fulfillment_jobs.id', ondelete="CASCADE"), nullable=False, index=True)

    direction = Column(String(20), nullable=False) # admin_to_customer, customer_to_admin, system
    kind = Column(String(30), nullable=False) # message, verification_request, customer_reply, status_change, completion

    encrypted_content = Column(Text, nullable=True)
    safe_preview = Column(Text, nullable=True)
    is_sensitive = Column(Boolean, nullable=False, default=False)

    telegram_message_id = Column(BigInteger, nullable=True)

    sent_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("direction IN ('admin_to_customer', 'customer_to_admin', 'system')", name='ck_moi_direction'),
        CheckConstraint("kind IN ('message', 'verification_request', 'customer_reply', 'status_change', 'completion')", name='ck_moi_kind'),
    )

    order = relationship("Order", backref=backref("interactions", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    job = relationship("ManualFulfillmentJob", backref=backref("interactions", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')

    def __str__(self):
        return f"Interaction #{self.id} ({self.kind})"


class ManualOrderNotification(Database.BASE):
    __tablename__ = 'manual_order_notifications'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id', ondelete="CASCADE"), nullable=False, index=True)
    fulfillment_job_id = Column(Integer, ForeignKey('manual_fulfillment_jobs.id', ondelete="CASCADE"), nullable=False, index=True)

    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)

    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name='ck_mon_status'),
    )

    order = relationship("Order", backref=backref("notifications", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    job = relationship("ManualFulfillmentJob", backref=backref("notifications", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')

    def __str__(self):
        return f"Notification #{self.id} ({self.status})"


class ManualOrderConversationSession(Database.BASE):
    __tablename__ = 'manual_order_conversation_sessions'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey('orders.id', ondelete="CASCADE"), nullable=False, index=True)
    fulfillment_job_id = Column(Integer, ForeignKey('manual_fulfillment_jobs.id', ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(20), nullable=False, default="active")

    opened_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_activity_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed', 'expired')", name='ck_mocs_status'),
        Index('ix_manual_order_conversation_sessions_active_user', 'telegram_id', unique=True, postgresql_where=text("status = 'active'"), sqlite_where=text("status = 'active'")),
    )

    user = relationship("User", backref=backref("conversation_sessions", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    order = relationship("Order", backref=backref("conversation_sessions", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')
    job = relationship("ManualFulfillmentJob", backref=backref("conversation_sessions", cascade="all, delete-orphan", passive_deletes=True), lazy='raise')

    def __str__(self):
        return f"Session #{self.id} ({self.status})"


async def register_models():
    async with Database().engine.begin() as conn:
        await conn.run_sync(Database.BASE.metadata.create_all)
    await Role.insert_roles()
