import os
from functools import lru_cache

from supabase import Client, create_client


class MissingConfigError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = _setting(name)
    if not value:
        raise MissingConfigError(f"Missing required environment variable: {name}")
    return value


def _setting(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets.get(name)
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = _required_env("SUPABASE_URL")
    key = _setting("SUPABASE_SERVICE_ROLE_KEY") or _required_env("SUPABASE_ANON_KEY")
    return create_client(url, key)


def get_state(key: str, default: str | None = None) -> str | None:
    response = (
        get_supabase()
        .table("app_state")
        .select("value")
        .eq("key", key)
        .limit(1)
        .execute()
    )
    if not response.data:
        return default
    return response.data[0]["value"]


def set_state(key: str, value: str) -> None:
    (
        get_supabase()
        .table("app_state")
        .upsert({"key": key, "value": value}, on_conflict="key")
        .execute()
    )
