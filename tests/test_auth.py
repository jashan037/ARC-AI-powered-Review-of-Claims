"""AUTH_MODE picks keys or Entra tokens; nothing here calls Azure."""
import dataclasses

from app import auth


def use(monkeypatch, mode):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_mode=mode, search_key="k" * 40, openai_key="o" * 40))


def test_key_mode_is_the_default_and_uses_the_keys(monkeypatch):
    assert auth.settings.auth_mode == "key"
    use(monkeypatch, "key")
    assert auth.search_credential().key == "k" * 40 and auth.openai_client_args() == {"api_key": "o" * 40}


def test_entra_mode_uses_tokens_and_no_key(monkeypatch):
    use(monkeypatch, "entra")
    cred = auth.search_credential()
    assert type(cred).__name__ == "DefaultAzureCredential" and not hasattr(cred, "key")
    args = auth.openai_client_args()
    assert list(args) == ["azure_ad_token_provider"] and callable(args["azure_ad_token_provider"]) and "o" * 40 not in repr(args)
