def mask_email(email: str) -> str:
    if not email or '@' not in email:
        return mask_generic(email)
    parts = email.split('@', 1)
    name, domain = parts[0], parts[1]
    if len(name) <= 2:
        masked_name = '*' * len(name)
    else:
        masked_name = f"{name[0]}***{name[-1]}"
    return f"{masked_name}@{domain}"

def mask_phone(phone: str) -> str:
    if not phone:
        return ""
    if len(phone) <= 6:
        return "*" * len(phone)
    # e.g. +962791234567 -> +962******567
    return f"{phone[:4]}******{phone[-3:]}"

def mask_username(username: str) -> str:
    if not username:
        return ""
    # Remove @ if present
    u = username.lstrip('@')
    if len(u) <= 2:
        return "*" * len(u)
    return f"{u[0]}***{u[-1]}"

def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return "*" * len(secret)

def mask_generic(text: str) -> str:
    if not text:
        return ""
    if len(text) <= 3:
        return "*" * len(text)
    if len(text) <= 8:
        return f"{text[0]}***{text[-1]}"
    return f"{text[:2]}***{text[-2:]}"

def mask_sensitive_data(text: str, field_type: str) -> str:
    if not text:
        return ""
    if field_type == 'email':
        return mask_email(text)
    elif field_type == 'phone':
        return mask_phone(text)
    elif field_type == 'username':
        return mask_username(text)
    elif field_type == 'secret':
        return mask_secret(text)
    return mask_generic(text)
