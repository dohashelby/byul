"""판정 플러그인 이름(CheckDef.judge.plugin) → 구현."""

from .base import Judge
from .declared import DeclaredJudge
from .rule_table import RuleTableJudge

JUDGES: dict[str, Judge] = {
    "rule_table": RuleTableJudge(),
    "declared": DeclaredJudge(),
}
