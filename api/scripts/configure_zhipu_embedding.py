"""Configure Zhipu embedding-3 as the default embedding model for a user."""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.security import encrypt_secret, mask_secret
from app.db.postgres import SessionLocal
from app.models.model_config_model import ModelConfig
from app.models.user_model import User


DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "embedding-3"
DEFAULT_NAME = "智谱 embedding-3"


async def _find_user(session, username: str | None) -> User:
    if username:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            raise SystemExit(f"User not found: {username}")
        return user

    result = await session.execute(select(User).order_by(User.created_at.asc()).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        raise SystemExit("No user exists. Register or log in once before configuring models.")
    return user


async def configure(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        user = await _find_user(session, args.username)

        await session.execute(
            select(ModelConfig).where(
                ModelConfig.user_id == user.id,
                ModelConfig.type == "embedding",
            )
        )
        result = await session.execute(
            select(ModelConfig).where(
                ModelConfig.user_id == user.id,
                ModelConfig.type == "embedding",
                ModelConfig.provider == "zhipu",
                ModelConfig.model_name == args.model,
            )
        )
        config = result.scalar_one_or_none()

        await session.execute(
            ModelConfig.__table__.update()
            .where(
                ModelConfig.user_id == user.id,
                ModelConfig.type == "embedding",
                ModelConfig.is_default.is_(True),
            )
            .values(is_default=False)
        )

        if config is None:
            config = ModelConfig(
                user_id=user.id,
                type="embedding",
                provider="zhipu",
                name=args.name,
                model_name=args.model,
                api_key_encrypted=encrypt_secret(args.api_key),
                base_url=args.base_url,
                capability=[],
                is_default=True,
            )
            session.add(config)
            action = "created"
        else:
            config.name = args.name
            config.api_key_encrypted = encrypt_secret(args.api_key)
            config.base_url = args.base_url
            config.capability = []
            config.is_default = True
            action = "updated"

        await session.commit()
        print(
            f"{action} default embedding config for user={user.username} "
            f"provider=zhipu model={args.model} key={mask_secret(args.api_key)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--username")
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    asyncio.run(configure(args))


if __name__ == "__main__":
    main()
