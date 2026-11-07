"""Start the local application. No project source or model API is accessed."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="项目学习证据助手：本地服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--deepseek-proxy-port', type=int, default=0,
                        help='显式使用127.0.0.1上的HTTP/混合代理端口；0为原直连，不改系统网络')
    parser.add_argument("--enable-paid-model", action="store_true",
                        help="显式授权当前DeepSeek首轮试验；默认不读密钥、不调用模型")
    parser.add_argument("--enable-query-model", action="store_true",
                        help="已确认查询结构与正文用途时启用；沿用同一累计费用账本")
    parser.add_argument("--enable-submission-model", action="store_true",
                        help="已确认提交结构与正文用途时启用；沿用同一累计费用账本")
    parser.add_argument("--enable-consumption-model", action="store_true",
                        help="已确认消息消费结构与正文用途时启用；沿用同一累计费用账本")
    parser.add_argument("--enable-judging-model", action="store_true",
                        help="已确认判题结构与正文用途时启用；沿用同一累计费用账本")
    parser.add_argument("--enable-followup-model", action="store_true",
                        help="已确认新增正文追问用途时启用；同时需要相应主题开关，沿用原累计预算")
    followup_modes = parser.add_mutually_exclusive_group()
    followup_modes.add_argument("--followup-strict-function", action="store_true",
                        help="仅追问使用固定strict函数运输；需要启用追问，沿用同一权限和账本")
    followup_modes.add_argument('--followup-two-phase', action='store_true',
                        help='仅追问使用先固定引用再写文的模式；需要追问和主题用途，预算不重置')
    parser.add_argument('--followup-claim-parts', action='store_true',
                        help='显式启用selected_parts_v1规划载体；只适用于已启用的两阶段追问')
    parser.add_argument('--followup-selected-table', action='store_true',
                        help='显式启用selected_table_v1选行表；只适用于两阶段追问，与claim-parts开关互斥')
    parser.add_argument('--followup-prose-kind', action='store_true',
                        help='显式启用kind_prose_v1正文载体；只适用于已启用的两阶段追问')
    parser.add_argument('--followup-scope-view', action='store_true',
                        help='显式附加受限Java词法位置视图；仅适用于两阶段追问，仍执行原预算与引用校验')
    parser.add_argument('--followup-scope-columns', action='store_true',
                        help='显式按列编码受限位置视图；需要two_phase，与原scope-view互斥，原预算及引用标准保持')
    parser.add_argument('--followup-index-columns', action='store_true',
                        help='显式无损索引列编码；只适用于two_phase，与两个旧scope开关互斥，预算引用保持')
    args = parser.parse_args()
    if args.followup_index_columns and (not args.followup_two_phase or args.followup_scope_view or args.followup_scope_columns):
        parser.error('索引列模式需要two_phase且不能同时选择两个旧scope开关；未读取密钥或创建授权。')
    if args.followup_scope_columns and (not args.followup_two_phase or args.followup_scope_view):
        parser.error('按列位置视图需要two_phase且不能同时选择原scope-view；未读取密钥或创建授权。')
    if args.followup_selected_table and (not args.followup_two_phase or args.followup_claim_parts):
        parser.error('选行表需要显式two_phase且不能同时启用claim-parts；未读取密钥或创建授权。')
    if args.followup_scope_view and not args.followup_two_phase:
        parser.error('位置视图需要显式two_phase；未读取密钥或创建授权。')
    if args.followup_prose_kind and not args.followup_two_phase:
        parser.error('正文类型载体需要显式two_phase；未读取密钥或创建授权。')
    if args.followup_claim_parts and not args.followup_two_phase:
        parser.error('定位片段载体需要显式two_phase；未读取密钥或创建授权。')
    if not 0 <= args.deepseek_proxy_port <= 65535 or (args.deepseek_proxy_port and not args.enable_paid_model):
        parser.error('本机代理端口无效或未显式启用模型；未读取密钥或创建授权。')
    if (args.followup_strict_function or args.followup_two_phase) and not args.enable_followup_model:
        parser.error("固定函数传输需要先启用追问用途；未读取密钥或创建授权。")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if (args.enable_query_model or args.enable_submission_model or args.enable_consumption_model or args.enable_judging_model or args.enable_followup_model) and not args.enable_paid_model:
        parser.error("章节用途需要同时显式启用付费模型；未读取密钥或创建授权。")
    if args.enable_followup_model and not any((args.enable_query_model, args.enable_submission_model, args.enable_consumption_model, args.enable_judging_model)):
        parser.error("追问用途同时需要至少一个已批准主题开关；未读取密钥或创建授权。")

    import uvicorn
    from project_evidence.app import create_app
    adapter = None
    if args.enable_paid_model:
        from project_evidence.spending import SpendingLedger, SpendingError, PROFILE
        from project_evidence.storage import ProjectStore
        from project_evidence.deepseek_adapter import DeepSeekAdapter
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            parser.error("请仅在本机设置DEEPSEEK_API_KEY；不要在命令行参数或聊天中粘贴密钥。")
        ledger = SpendingLedger(ProjectStore(ROOT / "data"))
        try:
            import json
            budget_path = ROOT / 'prices' / 'project-budget-authorization.json'
            trial_limit = 5_000_000
            if budget_path.exists():
                if (budget_path.is_symlink() or budget_path.resolve().parent != budget_path.parent
                        or budget_path.stat().st_size > 4096):
                    raise ValueError('Budget proof path not permitted')
                budget_proof = json.loads(budget_path.read_text(encoding='utf-8'))
                ledger.apply_project_budget_authorization(budget_proof, confirmed=True)
                trial_limit = budget_proof['authorized_cumulative_limit_microyuan']
            grant = ledger.authorize(confirmed=True, grant_id=PROFILE + "-initial", trial_limit=trial_limit)
            receipt_path=ROOT/'prices'/'deepseek-verified.json'
            if receipt_path.exists():
                import json
                if receipt_path.is_symlink() or receipt_path.resolve().parent!=receipt_path.parent or receipt_path.stat().st_size>8000:
                    raise ValueError('Price proof path not permitted')
                ledger.refresh_verified_price(json.loads(receipt_path.read_text(encoding='utf-8')))
            state = ledger.summary(grant)
            if state["status"] != "ready":
                parser.error("价格或费用回执尚未核对，请先查看费用说明；未启用模型。")
        except SpendingError as error:
            reasons = {
                "price_expired": "价格核验凭据已过期或有效期不合规；请先重新核验官方价格。未发送模型请求，不能据此判断密钥错误。",
                "price_change_review": "价格核验文件与已批准模型、官方来源或价格不一致；请先复核价格。未发送模型请求。",
                "cost_uncertain": "存在未核对的模型费用回执；请先完成对账。未发送模型请求。",
                "grant_conflict": "现有费用授权与启动配置不一致；原预算和记录保留，未发送模型请求。",
            }
            parser.error(reasons.get(error.code, "费用门禁未通过；原预算和记录保留，未发送模型请求。"))
        except (ValueError, TypeError):
            parser.error("本机价格核验文件或费用配置格式不符合条件；未发送模型请求。")
        try:
            network_options = {'proxy_port': args.deepseek_proxy_port} if args.deepseek_proxy_port else {}
            claim_options = ({'followup_claim_format': 'selected_table_v1'} if args.followup_selected_table else
                             {'followup_claim_format': 'selected_parts_v1'} if args.followup_claim_parts else {})
            prose_options = {'followup_prose_format': 'kind_prose_v1'} if args.followup_prose_kind else {}
            scope_options = ({'followup_scope_view': 'java_scope_edge_unit_index_columns_v1'} if args.followup_index_columns else
                             {'followup_scope_view': 'java_scope_edge_unit_columns_v1'} if args.followup_scope_columns else
                             {'followup_scope_view': 'java_scope_edge_view_v1'} if args.followup_scope_view else {})
            adapter = DeepSeekAdapter(key, ledger, grant, enable_query=args.enable_query_model, enable_submission=args.enable_submission_model,
                                      enable_consumption=args.enable_consumption_model, enable_judging=args.enable_judging_model,
                                      enable_followup=args.enable_followup_model,
                                      followup_protocol=('two_phase' if args.followup_two_phase else
                                                         'strict_function' if args.followup_strict_function else 'responses'), **network_options, **claim_options, **prose_options, **scope_options)
        except ValueError:
            parser.error("本机密钥格式或模型配置未通过检查；未发送模型请求。请仅在本机检查，不要发送密钥。")
    uvicorn.run(create_app(model_adapter=adapter), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
