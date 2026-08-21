"""Tenant-aware RBAC authorization with organization roles and resource inheritance."""
from __future__ import annotations

import uuid
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.document_model import Document
from app.models.image_model import Image
from app.models.enterprise_rbac_model import Organization, OrganizationMembership, RBACAuditEvent, RBACRole, ResourceGrant
from app.models.knowledge_base_model import KnowledgeBase

PERMISSIONS = {
    "*", "organization.manage", "member.manage", "role.manage", "audit.read",
    "knowledge_base.create", "knowledge_base.read", "knowledge_base.write", "knowledge_base.manage",
    "document.read", "document.write", "document.manage", "image.read", "image.write", "image.manage", "knowledge.query",
}
SYSTEM_ROLES = {
    "owner": ["*"],
    "admin": ["member.manage", "role.manage", "audit.read", "knowledge_base.create", "knowledge_base.read", "knowledge_base.write", "knowledge_base.manage", "document.read", "document.write", "document.manage", "image.read", "image.write", "image.manage", "knowledge.query"],
    "editor": ["knowledge_base.read", "knowledge_base.write", "document.read", "document.write", "image.read", "image.write", "knowledge.query"],
    "viewer": ["knowledge_base.read", "document.read", "image.read", "knowledge.query"],
    "auditor": ["knowledge_base.read", "document.read", "image.read", "audit.read"],
}


def validate_permissions(values: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    unknown = set(normalized) - PERMISSIONS
    if unknown:
        raise BizError(f"未知权限: {', '.join(sorted(unknown))}", code=3060)
    return normalized


class RBACService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def audit(self, organization_id: uuid.UUID, actor_user_id: uuid.UUID, action: str,
              resource_type: str, resource_id: object, detail: dict | None = None) -> None:
        self.session.add(RBACAuditEvent(
            organization_id=organization_id, actor_user_id=actor_user_id, action=action,
            resource_type=resource_type, resource_id=str(resource_id), detail_json=detail or {},
        ))

    async def create_organization(self, owner_id: uuid.UUID, name: str) -> Organization:
        org = Organization(name=name.strip(), owner_id=owner_id)
        self.session.add(org)
        await self.session.flush()
        roles = {}
        for role_name, permissions in SYSTEM_ROLES.items():
            role = RBACRole(organization_id=org.id, name=role_name, permissions=permissions, is_system=True)
            self.session.add(role)
            roles[role_name] = role
        await self.session.flush()
        self.session.add(OrganizationMembership(organization_id=org.id, user_id=owner_id, role_id=roles["owner"].id))
        self.audit(org.id, owner_id, "organization.create", "organization", org.id, {"name": org.name})
        await self.session.commit()
        await self.session.refresh(org)
        return org

    async def _membership(self, user_id: uuid.UUID, organization_id: uuid.UUID) -> tuple[OrganizationMembership, RBACRole] | None:
        row = (await self.session.execute(
            select(OrganizationMembership, RBACRole)
            .join(RBACRole, RBACRole.id == OrganizationMembership.role_id)
            .where(OrganizationMembership.organization_id == organization_id,
                   OrganizationMembership.user_id == user_id,
                   OrganizationMembership.status == "active")
        )).first()
        return row if row else None

    async def require_org(self, user_id: uuid.UUID, organization_id: uuid.UUID, permission: str) -> Organization:
        org = await self.session.get(Organization, organization_id)
        if not org:
            raise BizError("企业不存在", code=3061, status_code=404)
        if org.owner_id == user_id:
            return org
        member = await self._membership(user_id, organization_id)
        if not member:
            raise BizError("无企业资源访问权限", code=3062, status_code=403)
        if self._allows(member[1].permissions, permission):
            return org
        grants = await self._grants(organization_id, "organization", organization_id, user_id, member[1].id)
        if any(self._allows(grant.permissions, permission) for grant in grants):
            return org
        raise BizError("无企业资源访问权限", code=3062, status_code=403)

    async def require_delegable_permissions(
        self, user_id: uuid.UUID, organization_id: uuid.UUID, permissions: list[str]
    ) -> None:
        """Prevent role/grant managers from delegating privileges they do not hold."""
        org = await self.session.get(Organization, organization_id)
        if org and org.owner_id == user_id:
            return
        member = await self._membership(user_id, organization_id)
        if not member or any(
            not self._allows(member[1].permissions, permission) for permission in permissions
        ):
            raise BizError("不能授予超出自身范围的权限", code=3075, status_code=403)

    @staticmethod
    def _allows(permissions: list, permission: str) -> bool:
        values = set(permissions or [])
        if "*" in values or permission in values:
            return True
        prefix = permission.split(".", 1)[0]
        return f"{prefix}.manage" in values

    async def require_kb(self, user_id: uuid.UUID, kb_id: uuid.UUID, permission: str) -> KnowledgeBase:
        kb = await self.session.get(KnowledgeBase, kb_id)
        if not kb:
            raise BizError("知识库不存在", code=3040, status_code=404)
        if kb.user_id == user_id and not kb.organization_id:
            return kb
        if not kb.organization_id:
            raise BizError("无知识库访问权限", code=3063, status_code=403)
        org = await self.session.get(Organization, kb.organization_id)
        if org and org.owner_id == user_id:
            return kb
        member = await self._membership(user_id, kb.organization_id)
        if not member:
            raise BizError("无知识库访问权限", code=3063, status_code=403)
        role = member[1]
        if self._allows(role.permissions, permission):
            return kb
        org_grants = await self._grants(kb.organization_id, "organization", kb.organization_id, user_id, role.id)
        if any(self._allows(grant.permissions, permission) for grant in org_grants):
            return kb
        grants = await self._grants(kb.organization_id, "knowledge_base", kb.id, user_id, role.id)
        if any(self._allows(grant.permissions, permission) for grant in grants):
            return kb
        raise BizError("无知识库访问权限", code=3063, status_code=403)

    async def require_document(self, user_id: uuid.UUID, document_id: uuid.UUID, permission: str) -> Document:
        doc = await self.session.get(Document, document_id)
        if not doc:
            raise BizError("文档不存在", code=3006, status_code=404)
        kb = await self.session.get(KnowledgeBase, doc.kb_id) if doc.kb_id else None
        if doc.user_id == user_id and not (kb and kb.organization_id):
            return doc
        if not doc.kb_id:
            raise BizError("无文档访问权限", code=3064, status_code=403)
        if not kb or not kb.organization_id:
            raise BizError("无文档访问权限", code=3064, status_code=403)
        member = await self._membership(user_id, kb.organization_id)
        if not member:
            raise BizError("无文档访问权限", code=3064, status_code=403)
        if self._allows(member[1].permissions, permission):
            return doc
        grants = await self._grants(kb.organization_id, "document", doc.id, user_id, member[1].id)
        if any(self._allows(grant.permissions, permission) for grant in grants):
            return doc
        try:
            await self.require_kb(
                user_id, doc.kb_id, permission.replace("document.", "knowledge_base.")
            )
            return doc
        except BizError as exc:
            if exc.status_code != 403:
                raise
        raise BizError("无文档访问权限", code=3064, status_code=403)

    async def require_image(self, user_id: uuid.UUID, image_id: uuid.UUID, permission: str) -> Image:
        image = await self.session.get(Image, image_id)
        if not image:
            raise BizError("图片不存在", code=3022, status_code=404)
        kb = await self.session.get(KnowledgeBase, image.kb_id) if image.kb_id else None
        if image.user_id == user_id and not (kb and kb.organization_id):
            return image
        if not kb:
            raise BizError("无图片访问权限", code=3074, status_code=403)
        if not kb.organization_id:
            raise BizError("无图片访问权限", code=3074, status_code=403)
        member = await self._membership(user_id, kb.organization_id)
        if not member:
            raise BizError("无图片访问权限", code=3074, status_code=403)
        if self._allows(member[1].permissions, permission):
            return image
        grants = await self._grants(kb.organization_id, "image", image.id, user_id, member[1].id)
        if any(self._allows(grant.permissions, permission) for grant in grants):
            return image
        await self.require_kb(user_id, kb.id, permission.replace("image.", "knowledge_base."))
        return image

    async def _grants(self, org_id, resource_type, resource_id, user_id, role_id):
        return list((await self.session.scalars(
            select(ResourceGrant).where(ResourceGrant.organization_id == org_id,
                ResourceGrant.resource_type == resource_type, ResourceGrant.resource_id == resource_id,
                or_(
                    (ResourceGrant.principal_type == "user") & (ResourceGrant.principal_id == user_id),
                    (ResourceGrant.principal_type == "role") & (ResourceGrant.principal_id == role_id),
                ))
        )).all())

    async def allowed_kb_ids(self, user_id: uuid.UUID, permission: str = "knowledge.query") -> list[str]:
        owned = set(await self.session.scalars(select(KnowledgeBase.id).where(
            KnowledgeBase.user_id == user_id, KnowledgeBase.organization_id.is_(None)
        )))
        memberships = (await self.session.execute(
            select(OrganizationMembership, RBACRole)
            .join(RBACRole, RBACRole.id == OrganizationMembership.role_id)
            .where(OrganizationMembership.user_id == user_id, OrganizationMembership.status == "active")
        )).all()
        for membership, role in memberships:
            if self._allows(role.permissions, permission):
                owned.update(await self.session.scalars(select(KnowledgeBase.id).where(KnowledgeBase.organization_id == membership.organization_id)))
            grants = list(await self.session.scalars(select(ResourceGrant).where(
                ResourceGrant.organization_id == membership.organization_id,
                ResourceGrant.resource_type.in_(["organization", "knowledge_base", "document", "image"]),
                or_(
                    (ResourceGrant.principal_type == "user") & (ResourceGrant.principal_id == user_id),
                    (ResourceGrant.principal_type == "role") & (ResourceGrant.principal_id == role.id),
                ))))
            for grant in grants:
                if not self._allows(grant.permissions, permission):
                    continue
                if grant.resource_type == "organization":
                    owned.update(await self.session.scalars(select(KnowledgeBase.id).where(
                        KnowledgeBase.organization_id == membership.organization_id
                    )))
                elif grant.resource_type == "knowledge_base":
                    owned.add(grant.resource_id)
                elif grant.resource_type == "document":
                    kb_id = await self.session.scalar(
                        select(Document.kb_id).where(Document.id == grant.resource_id)
                    )
                    if kb_id:
                        owned.add(kb_id)
                else:
                    kb_id = await self.session.scalar(
                        select(Image.kb_id).where(Image.id == grant.resource_id)
                    )
                    if kb_id:
                        owned.add(kb_id)
        return [str(value) for value in owned]

    async def retrieval_scope(self, user_id: uuid.UUID) -> dict[str, list[str]]:
        """Return unrestricted KBs and individually granted sources separately.

        Keeping these sets separate prevents a document grant from accidentally
        exposing every other document in the same knowledge base.
        """
        full_kbs = set(await self.session.scalars(select(KnowledgeBase.id).where(
            KnowledgeBase.user_id == user_id, KnowledgeBase.organization_id.is_(None)
        )))
        source_ids: set[uuid.UUID] = set()
        visible_kbs = set(full_kbs)
        memberships = (await self.session.execute(
            select(OrganizationMembership, RBACRole)
            .join(RBACRole, RBACRole.id == OrganizationMembership.role_id)
            .where(OrganizationMembership.user_id == user_id, OrganizationMembership.status == "active")
        )).all()
        for membership, role in memberships:
            if self._allows(role.permissions, "knowledge.query"):
                ids = set(await self.session.scalars(select(KnowledgeBase.id).where(
                    KnowledgeBase.organization_id == membership.organization_id)))
                full_kbs.update(ids)
                visible_kbs.update(ids)
            grants = list(await self.session.scalars(select(ResourceGrant).where(
                ResourceGrant.organization_id == membership.organization_id,
                or_(
                    (ResourceGrant.principal_type == "user") & (ResourceGrant.principal_id == user_id),
                    (ResourceGrant.principal_type == "role") & (ResourceGrant.principal_id == role.id),
                ))))
            for grant in grants:
                if not self._allows(grant.permissions, "knowledge.query"):
                    continue
                if grant.resource_type == "organization":
                    ids = set(await self.session.scalars(select(KnowledgeBase.id).where(
                        KnowledgeBase.organization_id == membership.organization_id
                    )))
                    full_kbs.update(ids)
                    visible_kbs.update(ids)
                elif grant.resource_type == "knowledge_base":
                    full_kbs.add(grant.resource_id)
                    visible_kbs.add(grant.resource_id)
                elif grant.resource_type == "document":
                    source_ids.add(grant.resource_id)
                    kb_id = await self.session.scalar(select(Document.kb_id).where(Document.id == grant.resource_id))
                    if kb_id:
                        visible_kbs.add(kb_id)
                elif grant.resource_type == "image":
                    source_ids.add(grant.resource_id)
                    kb_id = await self.session.scalar(select(Image.kb_id).where(Image.id == grant.resource_id))
                    if kb_id:
                        visible_kbs.add(kb_id)
        return {
            "knowledge_base_ids": [str(value) for value in full_kbs],
            "source_ids": [str(value) for value in source_ids],
            "visible_knowledge_base_ids": [str(value) for value in visible_kbs],
        }
