
import pytest

def test_intake_validation():
    from bot.misc.intake_validator import validate_field_input
    from bot.database.models.main import ProductCustomerField

    f = ProductCustomerField(
        field_type='select',
        required=True,
        select_options_i18n={"opt1": {"en": "Option 1", "zh": "Option 1 ZH"}}
    )
    # canonical key "opt1" should be valid
    assert validate_field_input(f, "opt1") == "opt1"

    with pytest.raises(Exception):
        validate_field_input(f, "Option 1 ZH") # label is not a key
