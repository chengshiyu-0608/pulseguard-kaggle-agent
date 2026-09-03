from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolResult:
    name: str
    summary: str
    payload: dict


class RetentionAgent:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.users = json.loads((artifact_dir / "users.json").read_text(encoding="utf-8"))
        # Keep the prototype usable when an imported artifact has no factor array.
        for user in self.users:
            if not isinstance(user.get("factors"), list):
                user["factors"] = []
        self.user_map = {user["user_id"]: user for user in self.users}
        self.evaluation = json.loads(
            (artifact_dir / "evaluation.json").read_text(encoding="utf-8")
        )
        # Prototype state replaces a production database for the demo.
        self.status_map: dict[str, str] = {}
        self.records: list[dict] = []

    def list_users(self, risk_tier: str = "全部", query: str = "") -> list[dict]:
        users = self.users
        if risk_tier and risk_tier != "全部":
            users = [user for user in users if user["risk_tier"] == risk_tier]
        query = query.strip().upper()
        if query:
            users = [user for user in users if query in user["user_id"].upper()]
        return [
            {**user, "status": self.status_map.get(user["user_id"], "待处理")}
            for user in users
        ]

    def apply_action(self, user_id: str, action: str, channel: str = "") -> dict:
        if user_id not in self.user_map:
            raise KeyError(f"Unknown user: {user_id}")
        actions = {
            "generate": "生成运营建议",
            "send": "发送触达",
            "handled": "标记为已处理",
        }
        if action not in actions:
            raise ValueError("Unsupported action")
        status = "已触达" if action == "send" else "已处理" if action == "handled" else "待处理"
        self.status_map[user_id] = status
        record = {
            "user_id": user_id,
            "action": actions[action],
            "channel": channel or "站内通知 / 邮件提醒" if action == "send" else "",
            "status": status,
        }
        self.records.insert(0, record)
        return record

    def list_records(self) -> list[dict]:
        return self.records

    def get_profile(self, user_id: str) -> ToolResult:
        user = self.user_map[user_id]
        summary = (
            f"{user_id} 最近活跃月为 {user['month']}，当月提交 {user['submissions']} 次、"
            f"活跃 {user['active_days']} 天、参与 {user['active_competitions']} 个竞赛。"
        )
        return ToolResult("get_user_profile", summary, user)

    def calculate_risk(self, user_id: str) -> ToolResult:
        user = self.user_map[user_id]
        summary = (
            f"未来连续3个月沉默风险分数为 {user['risk_score'] * 100:.1f}%，"
            f"当前分层为{user['risk_tier']}。"
        )
        return ToolResult(
            "calculate_silence_risk",
            summary,
            {
                "risk_score": user["risk_score"],
                "risk_tier": user["risk_tier"],
                "model_test_auc": self.evaluation["test"]["roc_auc"],
            },
        )

    def explain_risk(self, user_id: str) -> ToolResult:
        user = self.user_map[user_id]
        increasing = [factor for factor in user["factors"] if factor["contribution"] > 0]
        decreasing = [factor for factor in user["factors"] if factor["contribution"] <= 0]
        parts = []
        if increasing:
            parts.append("主要风险信号：" + "、".join(item["label"] for item in increasing[:3]))
        if decreasing:
            parts.append("保护信号：" + "、".join(item["label"] for item in decreasing[:2]))
        summary = "；".join(parts) + "。"
        return ToolResult("explain_risk_factors", summary, {"factors": user["factors"]})

    def recommend_actions(self, user_id: str) -> ToolResult:
        user = self.user_map[user_id]
        actions = []
        active_days = user.get("active_days") or 0
        competitions = user.get("active_competitions") or 0
        momentum = user.get("recent_activity_momentum")
        sources = user.get("competition_sources") or 0
        historical_average = user.get("historical_average_submissions") or 0

        if active_days <= 2:
            actions.append("在7天内推送低门槛任务或短周期竞赛，降低再次参与成本")
        if competitions <= 1:
            actions.append("基于历史标签推荐2至3个相邻竞赛，扩大任务选择面")
        if momentum is not None and momentum < 0:
            actions.append("触发活跃下滑提醒，并提供上次竞赛的复盘入口")
        if sources <= 0:
            actions.append("推荐高质量Notebook、数据集和入门案例，补充任务资源连接")
        if historical_average >= 5 and active_days <= 2:
            actions.append("将用户标记为高价值回落用户，优先进入人工召回名单")
        if not actions:
            actions.append("维持常规任务推荐，并在下个自然月复核活跃趋势")

        return ToolResult(
            "generate_operation_actions",
            "；".join(actions[:4]) + "。",
            {"actions": actions[:4], "requires_human_review": True},
        )

    def diagnose(self, user_id: str, question: str = "") -> dict:
        if user_id not in self.user_map:
            raise KeyError(f"Unknown user: {user_id}")
        tools = [
            self.get_profile(user_id),
            self.calculate_risk(user_id),
            self.explain_risk(user_id),
            self.recommend_actions(user_id),
        ]
        answer = "\n".join(
            [
                tools[0].summary,
                tools[1].summary,
                tools[2].summary,
                "建议动作：" + tools[3].summary,
                "以上建议用于运营排查，执行前需由运营人员结合场景复核。",
            ]
        )
        return {
            "user_id": user_id,
            "question": question or "分析该用户的长期沉默风险并给出运营建议",
            "answer": answer,
            "mode": "offline_explainable_agent",
            "tool_trace": [
                {"tool": result.name, "summary": result.summary} for result in tools
            ],
            "profile": tools[0].payload,
            "risk": tools[1].payload,
            "explanation": tools[2].payload,
            "recommendation": tools[3].payload,
        }
