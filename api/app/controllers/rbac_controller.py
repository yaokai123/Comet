"""Enterprise organization, role, membership, and resource grant APIs."""
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.exceptions import BizError
from app.core.rbac import RBACService, validate_permissions
from app.core.response import success
from app.db.postgres import get_session
from app.models.enterprise_rbac_model import Organization, OrganizationMembership, RBACAuditEvent, RBACRole, ResourceGrant
from app.models.document_model import Document
from app.models.image_model import Image
from app.models.knowledge_base_model import KnowledgeBase
from app.models.user_model import User
from app.schemas.rbac_schema import MembershipUpsert, OrganizationCreate, ResourceGrantUpsert, RoleCreate

router = APIRouter(prefix="/organizations", tags=["enterprise_rbac"])


async def _validate_grant_scope(session: AsyncSession, org_id: uuid.UUID, body: ResourceGrantUpsert) -> None:
    if body.resource_type == "organization":
        valid_resource = body.resource_id == org_id
    elif body.resource_type == "knowledge_base":
        kb = await session.get(KnowledgeBase, body.resource_id)
        valid_resource = bool(kb and kb.organization_id == org_id)
    elif body.resource_type == "document":
        doc = await session.get(Document, body.resource_id)
        kb = await session.get(KnowledgeBase, doc.kb_id) if doc and doc.kb_id else None
        valid_resource = bool(kb and kb.organization_id == org_id)
    else:
        image = await session.get(Image, body.resource_id)
        kb = await session.get(KnowledgeBase, image.kb_id) if image and image.kb_id else None
        valid_resource = bool(kb and kb.organization_id == org_id)
    if not valid_resource:
        raise BizError("授权资源不属于当前企业", code=3072)
    if body.principal_type == "role":
        role = await session.get(RBACRole, body.principal_id)
        valid_principal = bool(role and role.organization_id == org_id)
    else:
        valid_principal = bool(await session.scalar(select(OrganizationMembership.id).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == body.principal_id,
            OrganizationMembership.status == "active")))
    if not valid_principal:
        raise BizError("授权主体不属于当前企业", code=3073)


@router.post("")
async def create_organization(body: OrganizationCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    org = await RBACService(session).create_organization(user.id, body.name)
    return success({"id": str(org.id), "name": org.name})


@router.get("")
async def list_organizations(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Organization).outerjoin(OrganizationMembership,
            (OrganizationMembership.organization_id == Organization.id) & (OrganizationMembership.user_id == user.id))
        .where((Organization.owner_id == user.id) | (OrganizationMembership.status == "active")).distinct()
    )).scalars().all()
    return success([{"id": str(row.id), "name": row.name, "owner_id": str(row.owner_id)} for row in rows])


@router.get("/{org_id}/roles")
async def list_roles(org_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await RBACService(session).require_org(user.id, org_id, "role.manage")
    rows = (await session.scalars(select(RBACRole).where(RBACRole.organization_id == org_id).order_by(RBACRole.name))).all()
    return success([{"id": str(r.id), "name": r.name, "description": r.description, "permissions": r.permissions, "is_system": r.is_system} for r in rows])


@router.post("/{org_id}/roles")
async def create_role(org_id: uuid.UUID, body: RoleCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    await service.require_org(user.id, org_id, "role.manage")
    permissions = validate_permissions(body.permissions)
    await service.require_delegable_permissions(user.id, org_id, permissions)
    role = RBACRole(organization_id=org_id, name=body.name.strip(), description=body.description, permissions=permissions)
    session.add(role)
    await session.flush()
    service.audit(org_id, user.id, "role.create", "role", role.id, {"name": role.name, "permissions": role.permissions})
    await session.commit()
    await session.refresh(role)
    return success({"id": str(role.id), "name": role.name, "permissions": role.permissions})


@router.put("/{org_id}/roles/{role_id}")
async def update_role(org_id: uuid.UUID, role_id: uuid.UUID, body: RoleCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    await service.require_org(user.id, org_id, "role.manage")
    role = await session.get(RBACRole, role_id)
    if not role or role.organization_id != org_id:
        raise BizError("角色不存在", code=3068, status_code=404)
    if role.is_system:
        raise BizError("系统角色不可修改，请创建自定义角色", code=3069)
    role.name, role.description = body.name.strip(), body.description
    role.permissions = validate_permissions(body.permissions)
    await service.require_delegable_permissions(user.id, org_id, role.permissions)
    service.audit(org_id, user.id, "role.update", "role", role.id, {"permissions": role.permissions})
    await session.commit()
    return success({"id": str(role.id), "name": role.name, "permissions": role.permissions})


@router.delete("/{org_id}/roles/{role_id}")
async def delete_role(org_id: uuid.UUID, role_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    await service.require_org(user.id, org_id, "role.manage")
    role = await session.get(RBACRole, role_id)
    if not role or role.organization_id != org_id:
        return success(message="角色已删除")
    if role.is_system:
        raise BizError("系统角色不可删除", code=3070)
    used = await session.scalar(select(OrganizationMembership.id).where(OrganizationMembership.role_id == role.id).limit(1))
    if used:
        raise BizError("角色仍被成员使用", code=3071)
    service.audit(org_id, user.id, "role.delete", "role", role.id, {"name": role.name})
    await session.delete(role)
    await session.commit()
    return success(message="角色已删除")


@router.get("/{org_id}/members")
async def list_members(org_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await RBACService(session).require_org(user.id, org_id, "member.manage")
    rows = (await session.execute(select(OrganizationMembership, RBACRole, User)
        .join(RBACRole, RBACRole.id == OrganizationMembership.role_id)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == org_id))).all()
    return success([{"user_id": str(member.user_id), "username": target.username, "role_id": str(role.id), "role": role.name, "status": member.status} for member, role, target in rows])


@router.put("/{org_id}/members")
async def upsert_member(org_id: uuid.UUID, body: MembershipUpsert, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    await service.require_org(user.id, org_id, "member.manage")
    role = await session.get(RBACRole, body.role_id)
    target = await session.get(User, body.user_id)
    if not role or role.organization_id != org_id or not target:
        raise BizError("用户或角色不存在", code=3065, status_code=404)
    org = await session.get(Organization, org_id)
    if org and body.user_id == org.owner_id and role.name != "owner":
        raise BizError("企业所有者必须保留 owner 角色", code=3077)
    if role.name == "owner" and (not org or org.owner_id != user.id):
        raise BizError("只有企业所有者可以授予 owner 角色", code=3076, status_code=403)
    await service.require_delegable_permissions(user.id, org_id, role.permissions)
    membership = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.organization_id == org_id, OrganizationMembership.user_id == body.user_id))
    if membership:
        membership.role_id, membership.status = role.id, "active"
    else:
        membership = OrganizationMembership(organization_id=org_id, user_id=body.user_id, role_id=role.id)
        session.add(membership)
    service.audit(org_id, user.id, "member.upsert", "user", body.user_id, {"role_id": str(role.id)})
    await session.commit()
    return success({"user_id": str(body.user_id), "role_id": str(role.id)})


@router.delete("/{org_id}/members/{member_user_id}")
async def remove_member(org_id: uuid.UUID, member_user_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    org = await service.require_org(user.id, org_id, "member.manage")
    if org.owner_id == member_user_id:
        raise BizError("不能移除企业所有者", code=3066)
    membership = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.organization_id == org_id, OrganizationMembership.user_id == member_user_id))
    if membership:
        service.audit(org_id, user.id, "member.remove", "user", member_user_id)
        await session.delete(membership)
        await session.commit()
    return success(message="成员已移除")


@router.get("/{org_id}/grants")
async def list_grants(org_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await RBACService(session).require_org(user.id, org_id, "role.manage")
    rows = (await session.scalars(select(ResourceGrant).where(ResourceGrant.organization_id == org_id))).all()
    return success([{"id": str(g.id), "resource_type": g.resource_type, "resource_id": str(g.resource_id), "principal_type": g.principal_type, "principal_id": str(g.principal_id), "permissions": g.permissions} for g in rows])


@router.put("/{org_id}/grants")
async def upsert_grant(org_id: uuid.UUID, body: ResourceGrantUpsert, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    await service.require_org(user.id, org_id, "role.manage")
    if body.resource_type not in {"organization", "knowledge_base", "document", "image"} or body.principal_type not in {"user", "role"}:
        raise BizError("授权资源或主体类型无效", code=3067)
    await _validate_grant_scope(session, org_id, body)
    permissions = validate_permissions(body.permissions)
    await service.require_delegable_permissions(user.id, org_id, permissions)
    grant = await session.scalar(select(ResourceGrant).where(
        ResourceGrant.organization_id == org_id, ResourceGrant.resource_type == body.resource_type,
        ResourceGrant.resource_id == body.resource_id, ResourceGrant.principal_type == body.principal_type,
        ResourceGrant.principal_id == body.principal_id))
    if grant:
        grant.permissions = permissions
    else:
        grant = ResourceGrant(organization_id=org_id, created_by=user.id, **body.model_dump())
        grant.permissions = permissions
        session.add(grant)
    service.audit(org_id, user.id, "grant.upsert", body.resource_type, body.resource_id,
                  {"principal_type": body.principal_type, "principal_id": str(body.principal_id), "permissions": grant.permissions})
    await session.commit()
    await session.refresh(grant)
    return success({"id": str(grant.id), "permissions": grant.permissions})


@router.delete("/{org_id}/grants/{grant_id}")
async def delete_grant(org_id: uuid.UUID, grant_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    service = RBACService(session)
    await service.require_org(user.id, org_id, "role.manage")
    grant = await session.get(ResourceGrant, grant_id)
    if grant and grant.organization_id == org_id:
        service.audit(org_id, user.id, "grant.delete", grant.resource_type, grant.resource_id, {"grant_id": str(grant.id)})
        await session.delete(grant)
        await session.commit()
    return success(message="授权已删除")


@router.get("/{org_id}/audit-events")
async def list_audit_events(org_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await RBACService(session).require_org(user.id, org_id, "audit.read")
    rows = (await session.scalars(select(RBACAuditEvent).where(
        RBACAuditEvent.organization_id == org_id).order_by(RBACAuditEvent.created_at.desc()).limit(500))).all()
    return success([{"id": str(row.id), "actor_user_id": str(row.actor_user_id), "action": row.action,
        "resource_type": row.resource_type, "resource_id": row.resource_id, "detail": row.detail_json,
        "created_at": row.created_at.isoformat()} for row in rows])
