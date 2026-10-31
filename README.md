# PDB Affinity Curation MCP

一个面向 PDB 结构–化合物亲和力/活性数据整理的可审计 MCP 与配套 Skills。

核心原则：

> 最终数据宁缺勿错；每个缺失必须能回溯到查了哪里、为何仍缺。

本项目把文献与补充材料采集、页面级 OCR/视觉复核、化合物与构体身份解析、
数据库交叉核查、缺失处置账本、严格验证和人工审核比较串成一条工作流。
数据库命中只算线索，不能直接成为接受的亲和力数据。

## 仓库内容

```text
mcp/pdb-curation-mcp/      零依赖 Python stdio MCP
skills/pdb-evidence-harvester/
skills/pdb-affinity-analysis/
skills/pdb-affinity-output/
skills/pdb-affinity-curation/
skills/pdb-affinity-mcp/
```

配套行为 Skill 也随 MCP 保留一份自包含副本：

```text
mcp/pdb-curation-mcp/skill/pdb-affinity-mcp/
```

详细设计和操作规则见
[MCP README](mcp/pdb-curation-mcp/README.md)。

## MCP 能力

- RCSB、Europe PMC、开放获取全文和补充材料发现；
- Zotero、Downloads 和项目目录中的本地文献匹配；
- PDF/DOCX/TIFF/ZIP 的页面级文本、OCR 共识和视觉复核队列；
- PDF、表格、图和补充材料中的精确 page/table/figure locator；
- 化合物、HET、InChIKey、CAS、构体和突变上下文核查；
- Reaxys、SciFinder、ChEMBL、BindingDB 多源交叉核查账本；
- 结构化缺失原因、检索范围、证据收据、`Query_ID` 和 `Blocker_ID`；
- 表到缺失账本的 `PDB + ligand + context + field` 覆盖审计；
- 严格 schema、人工审核比较、blocker closure、visual receipt 和最终验收；
- SHA-256 run manifest、并发账本写锁和长任务取消。

## 运行要求

- Python 3.11+
- 可选文档依赖：`pypdf`、`openpyxl`、`python-docx`、`Pillow`、`requests`
- 可选系统工具：Poppler、Tesseract、PowerShell OCR
- 可选专利抽取：`PATMAP_API_KEY`
- 可选浏览器文献路径：Node.js + Playwright/授权浏览器会话

SciFinder、Reaxys 和 CAS BioFinder 需要机构授权。普通网页登录不等价于
API entitlement。项目不会绕过付费墙、CAPTCHA 或机构许可。

## 快速开始

本项目按源码仓库运行，不发布 PyPI/wheel 包。克隆仓库后，从 MCP 目录运行：

```powershell
cd mcp/pdb-curation-mcp
python -m pip install -r requirements-optional.txt
python server.py --self-test
python -m unittest discover tests -v
```

将仓库的 `skills/` 安装或链接到 Codex 的 Skill 根目录，例如：

```text
~/.codex/skills/
~/.agents/skills/
```

把 `mcp/pdb-curation-mcp/skill/pdb-affinity-mcp/` 同时安装到 Skill 根目录。

MCP 客户端示例：

```json
{
  "mcpServers": {
    "pdb-curation": {
      "command": "python",
      "args": [
        "/path/to/pdb-affinity-curation-mcp/mcp/pdb-curation-mcp/server.py"
      ],
      "env": {
        "PDB_SKILL_ROOT": "/path/to/pdb-affinity-curation-mcp/skills",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

Windows 下把 `/path/to/...` 替换为实际绝对路径。

## 推荐工作流

1. 环境检查与授权路径确认；
2. 构建 PDB 证据包、匹配本地全文、补充材料和专利候选；
3. 页面级提取、OCR、视觉复核；
4. 精确解析化合物、构体、突变和测量类型；
5. 授权数据库查询并记录 export、scope 和 SHA-256；
6. 为每个空白记录结构化原因、locator 或 blocker；
7. 审计 traceability 与表级 coverage；
8. 严格验证、人工审核比较和最终验收；
9. 生成 run manifest。

## 隐私与数据边界

- 仓库不包含论文 PDF、补充材料、数据库导出、人工审核表或本地运行产物。
- 不提交用户名、密码、session cookie、API token、私钥、机构 IP 或内部路径。
- 使用者在自己的授权环境中提供文献和数据库访问。
- 外部数据库、出版社和专利服务仍受各自条款约束。

安全问题请见 [SECURITY.md](SECURITY.md)。

### 本地执行边界

该 MCP 面向单用户本地 stdio，不是网络服务。工具接受调用者提供的文件路径，
因此应在隔离工作目录或受限沙箱内运行，不要把 MCP 直接暴露给不可信用户。

包装脚本只接收允许列表中的进程环境；`PATMAP_API_KEY` 只传给 PatMap 工具。
`patmap_extract` 会把本地 PDF 上传到配置的第三方 PatMap 服务，使用前必须取得
明确的处理和上传授权。

`environment_check` 可能显示本地路径或是否存在某些凭据。不要把它的原始输出
直接发布到 Issue、日志或公开 CI 产物。

## 验证

当前 MCP 自动化测试覆盖协议协商、两种 stdio framing、工具 schema、并发账本、
取消、缺失原因、blocker/query 外键、表级覆盖和 manifest。测试不附带任何
私有研究数据。

## 许可

代码与文档使用 MIT License，见 [LICENSE](LICENSE)。第三方论文、数据库、
专利内容不包含在本仓库中，也不受本仓库许可证重新授权。

