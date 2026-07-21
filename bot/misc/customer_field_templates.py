from typing import Any

def get_field_templates() -> dict[str, list[dict[str, Any]]]:
    """Returns canonical definitions for Quick Field Sets."""
    return {
        "Email Only": [
            {
                "field_key": "email",
                "field_type": "email",
                "required": True,
                "is_sensitive": True,
                "is_active": True,
                "max_length": 254,
                "label_i18n": {
                    "en": "Email address",
                    "ar": "البريد الإلكتروني"
                },
                "placeholder_i18n": {
                    "en": "name@example.com",
                    "ar": "name@example.com"
                },
                "help_text_i18n": {
                    "en": "Enter the email linked to the account.",
                    "ar": "أدخل البريد الإلكتروني المرتبط بالحساب."
                }
            }
        ],
        "Email + Password": [
            {
                "field_key": "email",
                "field_type": "email",
                "required": True,
                "is_sensitive": True,
                "is_active": True,
                "max_length": 254,
                "label_i18n": {
                    "en": "Email address",
                    "ar": "البريد الإلكتروني"
                },
                "placeholder_i18n": {
                    "en": "name@example.com",
                    "ar": "name@example.com"
                },
                "help_text_i18n": {
                    "en": "Enter the email linked to the account.",
                    "ar": "أدخل البريد الإلكتروني المرتبط بالحساب."
                }
            },
            {
                "field_key": "password",
                "field_type": "secret",
                "required": True,
                "is_sensitive": True,
                "is_active": True,
                "max_length": 256,
                "label_i18n": {
                    "en": "Account password",
                    "ar": "كلمة مرور الحساب"
                },
                "placeholder_i18n": {
                    "en": "Enter your password",
                    "ar": "أدخل كلمة المرور"
                },
                "help_text_i18n": {
                    "en": "Enter the account password. Never send an OTP, recovery code, or payment-card information.",
                    "ar": "أدخل كلمة مرور الحساب. لا ترسل رمز التحقق أو رمز الاسترداد أو معلومات البطاقة البنكية."
                }
            }
        ],
        "Username + Password": [
            {
                "field_key": "username",
                "field_type": "username",
                "required": True,
                "is_sensitive": False,
                "is_active": True,
                "max_length": 64,
                "label_i18n": {
                    "en": "Username",
                    "ar": "اسم المستخدم"
                },
                "placeholder_i18n": {
                    "en": "@username",
                    "ar": "@username"
                },
                "help_text_i18n": {
                    "en": "Enter the username for the account or platform.",
                    "ar": "أدخل اسم المستخدم الخاص بالحساب أو المنصة."
                }
            },
            {
                "field_key": "password",
                "field_type": "secret",
                "required": True,
                "is_sensitive": True,
                "is_active": True,
                "max_length": 256,
                "label_i18n": {
                    "en": "Account password",
                    "ar": "كلمة مرور الحساب"
                },
                "placeholder_i18n": {
                    "en": "Enter your password",
                    "ar": "أدخل كلمة المرور"
                },
                "help_text_i18n": {
                    "en": "Enter the account password. Never send an OTP, recovery code, or payment-card information.",
                    "ar": "أدخل كلمة مرور الحساب. لا ترسل رمز التحقق أو رمز الاسترداد أو معلومات البطاقة البنكية."
                }
            }
        ],
        "Account URL": [
            {
                "field_key": "account_url",
                "field_type": "url",
                "required": True,
                "is_sensitive": False,
                "is_active": True,
                "label_i18n": {
                    "en": "Account URL",
                    "ar": "رابط الحساب"
                },
                "placeholder_i18n": {
                    "en": "https://example.com/username",
                    "ar": "https://example.com/username"
                },
                "help_text_i18n": {
                    "en": "Enter the full link to the account.",
                    "ar": "أدخل الرابط الكامل للحساب."
                }
            }
        ],
        "Phone Number": [
            {
                "field_key": "phone",
                "field_type": "phone",
                "required": True,
                "is_sensitive": True,
                "is_active": True,
                "label_i18n": {
                    "en": "Phone number",
                    "ar": "رقم الهاتف"
                },
                "placeholder_i18n": {
                    "en": "+1234567890",
                    "ar": "+1234567890"
                },
                "help_text_i18n": {
                    "en": "Enter the phone number including country code.",
                    "ar": "أدخل رقم الهاتف متضمناً رمز الدولة."
                }
            }
        ]
    }
