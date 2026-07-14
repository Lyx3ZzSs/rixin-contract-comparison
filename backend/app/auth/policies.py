from __future__ import annotations

from app.auth.models import AGENT_ADMIN, APP_ROLES, CurrentUser
from app.models import CompareTask


class TaskAccessPolicy:
    def can_read(self, task: CompareTask, user: CurrentUser) -> bool:
        if not task.owner_sub:
            return False
        return user.has_any_role(AGENT_ADMIN) or task.owner_sub == user.sub

    def can_mutate(self, task: CompareTask, user: CurrentUser) -> bool:
        return self.can_read(task, user) and user.has_any_role(*APP_ROLES)

    def filter_visible(self, tasks: list[CompareTask], user: CurrentUser) -> list[CompareTask]:
        return [task for task in tasks if self.can_read(task, user)]
