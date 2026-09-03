# 「地量点火」样本外复判的定时入口。
#
# 规则 2026-09-03 进排序时证据是边缘的（匹配对照增量 +0.16pp，但 CI 下沿为负、
# 去右尾后转负，且是同批 11 条变体里挑出来的那条）。对「挑出来的那条」只有换一批
# 没见过的数据才算独立复核 —— 线上留痕就是那批数据，这个任务负责定期去看。
#
# 每周跑一次而不是等到十月一次性看：样本不够时脚本自己拒绝下结论、只报进度，
# 所以每周跑没有「过早定论」的风险，反而能早点发现另一种失败 —— 规则几乎从不触发。
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
& $python "experiments\ignite_review.py"
