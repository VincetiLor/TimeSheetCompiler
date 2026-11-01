from django.db import migrations

from authentication.services import decrypt_token, encrypt_token


def encrypt_tokens(apps, schema_editor):
    SocialToken = apps.get_model('socialaccount', 'SocialToken')
    for token in SocialToken.objects.all():
        token.token = encrypt_token(token.token)
        token.token_secret = encrypt_token(token.token_secret)
        token.save(update_fields=['token', 'token_secret'])


def decrypt_tokens(apps, schema_editor):
    SocialToken = apps.get_model('socialaccount', 'SocialToken')
    for token in SocialToken.objects.all():
        token.token = decrypt_token(token.token)
        token.token_secret = decrypt_token(token.token_secret)
        token.save(update_fields=['token', 'token_secret'])


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0001_initial'),
        ('socialaccount', '0006_alter_socialaccount_extra_data'),
    ]

    operations = [
        migrations.RunPython(encrypt_tokens, decrypt_tokens),
    ]
