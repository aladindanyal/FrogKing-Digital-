
import pytest
from bot.web.admin import StoreSettingsAdmin, CategoryAdmin, GoodsAdmin, MainMenuButtonSettingsAdmin, ProductCustomerFieldAdmin

def test_sqladmin_paths():
    assert len(StoreSettingsAdmin.column_details_list) == 22 + 25
    assert len(CategoryAdmin.column_details_list) == 11 + 10
    assert len(GoodsAdmin.column_details_list) == 17 + 10
    assert 'label_vi' in [c.name for c in MainMenuButtonSettingsAdmin.form_columns]
