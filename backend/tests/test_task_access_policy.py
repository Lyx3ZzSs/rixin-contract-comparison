from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import CompareTask

from auth_helpers import ADMIN, MANAGER, NO_ROLE, USER_A, USER_B


def owned_task(task_id: str, owner_sub: str) -> CompareTask:
    return CompareTask(task_id=task_id, owner_sub=owner_sub)


def test_owner_can_read_and_mutate_own_task() -> None:
    from app.auth.policies import TaskAccessPolicy

    task = owned_task("TA", USER_A.sub)
    assert TaskAccessPolicy().can_read(task, USER_A)
    assert TaskAccessPolicy().can_mutate(task, USER_A)


def test_other_user_and_manager_cannot_read_task() -> None:
    from app.auth.policies import TaskAccessPolicy

    task = owned_task("TA", USER_A.sub)
    assert not TaskAccessPolicy().can_read(task, USER_B)
    assert not TaskAccessPolicy().can_read(task, MANAGER)


def test_admin_can_read_new_owned_task_but_not_legacy_task() -> None:
    from app.auth.policies import TaskAccessPolicy

    policy = TaskAccessPolicy()
    assert policy.can_read(owned_task("TA", USER_A.sub), ADMIN)
    assert not policy.can_read(CompareTask(task_id="TLEGACY"), ADMIN)


def test_no_role_owner_can_read_but_cannot_mutate() -> None:
    from app.auth.policies import TaskAccessPolicy

    task = owned_task("TN", NO_ROLE.sub)
    assert TaskAccessPolicy().can_read(task, NO_ROLE)
    assert not TaskAccessPolicy().can_mutate(task, NO_ROLE)


def test_owner_sub_is_immutable() -> None:
    task = owned_task("TA", USER_A.sub)

    with pytest.raises(ValidationError):
        task.owner_sub = USER_B.sub
