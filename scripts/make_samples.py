"""样例合同生成器。

产出（``samples/``）
------------------
| 文件 | 用途 |
|---|---|
| 软件采购合同_风险版.docx | 场景 A：知识产权归供应商 + 到货即付全款无验收 |
| 软件采购合同_标准版.docx | 对照组，条款完整无风险 |
| 大额销售合同_风险版.pdf  | 场景 B：违约责任严重不对等 |
| 大额销售合同_标准版.pdf  | 对照组 |
| 销售合同_扫描件.png     | 清晰扫描件，走 OCR 路径 |
| 销售合同_严重模糊.png   | 低分辨率，触发 blocked（严重模糊） |
| 加密合同_模拟.docx      | 带真实 OLE 复合文档头，触发 blocked（已加密） |
| 空正文合同.docx         | 无可解析文本，触发 blocked（正文为空） |

用法::

    .venv\\Scripts\\python.exe scripts/make_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

BASE = Path(__file__).resolve().parents[1]
SAMPLES = BASE / "samples"
sys.path.insert(0, str(BASE))

# OLE 复合文档魔数 —— 真实的加密 docx 与旧版 doc 都是这个文件头
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ======================================================================
# 合同正文（结构化：段落列表 + 表格）
# ======================================================================
def _software_purchase_risky() -> dict:
    """场景 A：软件采购合同 · 风险版。"""
    return {
        "title": "软件采购合同",
        "contract_no": "CG-2026-0815-0027",
        "clauses": [
            ("第一条 合同标的", [
                "1.1 甲方向乙方采购「智维企业设备管理平台 V3.0」软件一套，"
                "包含平台基础模块、移动端应用及配套部署服务。",
                "1.2 交付内容包括：安装介质、部署文档、管理员操作手册各一份。",
                "1.3 本合同项下软件的具体功能清单以附件一《功能需求确认书》为准。",
            ]),
            ("第二条 合同金额与付款方式", [
                "2.1 本合同总金额为人民币肆拾捌万元整（￥480,000.00），含增值税专用发票。",
                "2.2 付款方式：甲方应于软件到货之日起十个工作日内，向乙方支付合同总金额的 100%，"
                "即人民币肆拾捌万元整。",
                "2.3 甲方逾期付款的，每逾期一日按未付款项的万分之五向乙方支付违约金。",
            ]),
            ("第三条 知识产权", [
                "3.1 本合同项下软件产品及其全部技术资料、源代码、算法模型、"
                "衍生开发成果的知识产权，均归乙方所有。",
                "3.2 甲方仅在本合同约定的使用范围内享有非独占的、不可转让的使用权。",
                "3.3 甲方基于使用软件所产生的业务数据，乙方有权在脱敏后用于产品优化与商业分析。",
            ]),
            ("第四条 交付与安装", [
                "4.1 乙方应于本合同生效之日起三十日内完成软件交付与本地部署。",
                "4.2 甲方应提供符合软件运行要求的服务器环境与网络条件。",
            ]),
            ("第五条 保密义务", [
                "5.1 双方应对因履行本合同而知悉的对方商业秘密承担保密义务。",
                "5.2 未经对方书面同意，任何一方不得向第三方披露前述保密信息。",
            ]),
            ("第六条 违约责任", [
                "6.1 乙方未按期交付的，每逾期一日按合同总金额的万分之一向甲方支付违约金。",
                "6.2 甲方违反本合同约定的，应赔偿乙方由此遭受的全部损失，"
                "包括但不限于直接损失、可得利益损失及乙方为实现债权支出的律师费、诉讼费。",
            ]),
            ("第七条 争议解决", [
                "7.1 因本合同产生的争议，双方应友好协商解决；协商不成的，"
                "提交乙方所在地人民法院诉讼解决。",
                "7.2 本合同适用中华人民共和国法律。",
            ]),
            ("第八条 其他", [
                "8.1 本合同自双方法定代表人或授权代表签字并加盖公章之日起生效。",
                "8.2 本合同一式肆份，甲乙双方各执贰份，具有同等法律效力。",
            ]),
        ],
        "parties": (
            "甲方（采购方）：启元智能装备制造有限公司",
            "乙方（供应方）：云枢软件技术（上海）有限公司",
        ),
        "uscc": "91310115MA1K3Q8X7D",
        "period": "自 2026 年 9 月 1 日至 2027 年 8 月 31 日",
        "table": {
            "caption": "附件一 功能清单",
            "header": ["序号", "功能模块", "功能说明", "是否必选"],
            "rows": [
                ["1", "设备台账管理", "设备全生命周期档案维护", "是"],
                ["2", "点检与保养", "计划制定、任务派发、记录归档", "是"],
                ["3", "故障工单", "报修、派工、验收闭环", "是"],
                ["4", "数据看板", "设备稼动率与维修成本分析", "否"],
            ],
        },
    }


def _software_purchase_standard() -> dict:
    """场景 A 的标准版：什么风险都没有，用来证明引擎不会误报。"""
    d = _software_purchase_risky()
    d["clauses"] = [
        ("第一条 合同标的", [
            "1.1 甲方向乙方采购「智维企业设备管理平台 V3.0」软件一套及其部署实施服务。",
            "1.2 交付内容包括安装介质、部署文档、管理员操作手册及源代码托管凭证各一份。",
        ]),
        ("第二条 合同金额与付款方式", [
            "2.1 本合同总金额为人民币肆拾捌万元整（￥480,000.00），含增值税专用发票。",
            "2.2 付款按里程碑分三期支付：合同生效后十个工作日内支付 30% 预付款；"
            "系统部署完成并通过初步验收后支付 40%；"
            "终验合格且质保期届满后十个工作日内支付剩余 30%。",
            "2.3 乙方应在每期收款前向甲方开具等额增值税专用发票，未开票的甲方有权暂缓支付。",
        ]),
        ("第三条 知识产权", [
            "3.1 乙方软件产品原有的知识产权归乙方所有；甲方基于本合同需求定制的功能模块、"
            "以及实施过程中形成的衍生开发成果，其知识产权归甲方所有。",
            "3.2 乙方应在交付时向甲方交付完整的源代码及编译说明，并授予甲方在本项目范围内"
            "永久、不可撤销的使用与二次开发权利。",
            "3.3 甲方业务数据的全部权利归甲方所有，乙方不得留存、转让或用于本合同之外的用途。",
        ]),
        ("第四条 交付与验收", [
            "4.1 乙方应于本合同生效之日起三十日内完成软件交付与本地部署。",
            "4.2 系统部署完成后，双方应在五个工作日内组织初步验收；"
            "初验合格后进入三十日试运行期，试运行期间系统可用率不低于 99.5% 的，"
            "由甲方出具终验合格确认书。",
            "4.3 未通过验收的，乙方应在十个工作日内完成整改并重新提交验收；"
            "经两次整改仍未通过的，甲方有权解除合同并要求乙方全额退款。",
        ]),
        ("第五条 保密义务", [
            "5.1 双方应对因履行本合同而知悉的对方商业秘密承担保密义务，保密期限为"
            "本合同终止后五年。",
            "5.2 接收方应在披露方提出要求后十个工作日内返还或销毁全部保密资料。",
        ]),
        ("第六条 数据安全与个人信息保护", [
            "6.1 乙方处理甲方数据应遵守《网络安全法》《数据安全法》《个人信息保护法》"
            "的相关规定，不得将数据出境。",
            "6.2 发生数据泄露事件的，乙方应在 24 小时内书面通知甲方，"
            "并承担由此产生的全部损失。",
        ]),
        ("第七条 违约责任", [
            "7.1 任一方违约的，应向守约方支付违约金，违约金按未履行部分金额每日万分之一"
            "计算，累计不超过合同总金额的 20%，双方对等适用。",
            "7.2 给对方造成损失超过违约金的，超出部分的赔偿责任以合同总金额的 30% 为上限，"
            "双方对等适用。",
        ]),
        ("第八条 不可抗力", [
            "8.1 因不可抗力不能履行合同的，遭受方应在事件发生后五日内书面通知对方，"
            "并在十五日内提供有效证明，否则不得主张免责。",
        ]),
        ("第九条 争议解决", [
            "9.1 因本合同产生的争议，双方应友好协商解决；协商不成的，"
            "提交甲方所在地有管辖权的人民法院诉讼解决。",
            "9.2 本合同适用中华人民共和国法律。",
        ]),
        ("第十条 其他", [
            "10.1 本合同自双方法定代表人或授权代表签字并加盖公章之日起生效。",
            "10.2 本合同一式肆份，甲乙双方各执贰份，具有同等法律效力。",
        ]),
    ]
    return d


def _sales_risky() -> dict:
    """场景 B：大额销售合同 · 风险版。"""
    return {
        "title": "产品销售合同",
        "contract_no": "XS-2026-0901-0114",
        "clauses": [
            ("第一条 产品与数量", [
                "1.1 甲方向乙方销售工业级精密轴承共计 12,000 套，型号规格见附件清单。",
                "1.2 产品技术标准执行国家标准 GB/T 307.1-2017。",
            ]),
            ("第二条 合同金额与结算", [
                "2.1 本合同总金额为人民币壹仟贰佰万元整（￥12,000,000.00）。",
                "2.2 乙方应于每月 25 日前完成当月对账，并在次月 15 日前支付当月货款。",
                "2.3 乙方逾期付款的，每逾期一日按未付款项的万分之一向甲方支付滞纳金。",
            ]),
            ("第三条 交付与验收", [
                "3.1 甲方应于收到订单后二十日内完成发货，运输费用由甲方承担。",
                "3.2 乙方应在收货后七日内完成验收，逾期未提出异议的视为验收合格。",
            ]),
            ("第四条 违约责任", [
                "4.1 乙方逾期付款的，按本合同第 2.3 条约定支付滞纳金，"
                "滞纳金累计上限为逾期金额的 1%。",
                "4.2 甲方交付的产品存在质量问题的，甲方应赔偿乙方因此遭受的全部损失，"
                "包括但不限于生产停工损失、客户索赔损失、商誉损失及可得利益损失，"
                "赔偿责任不设上限。",
                "4.3 甲方逾期交货的，每逾期一日按该批货物金额的万分之五向乙方支付违约金，"
                "上不封顶。",
            ]),
            ("第五条 保密与知识产权", [
                "5.1 双方对履约过程中知悉的对方商业信息承担保密义务。",
            ]),
            ("第六条 争议解决", [
                "6.1 因本合同产生的争议，提交乙方所在地人民法院诉讼解决。",
            ]),
            ("第七条 其他", [
                "7.1 本合同自双方盖章之日起生效，有效期为一年，期满后自动续约一年。",
            ]),
        ],
        "parties": (
            "甲方（销售方）：北方精工机械制造股份有限公司",
            "乙方（采购方）：宏远国际供应链管理有限公司",
        ),
        "uscc": "91110108MA01Y6H23K",
        "period": "自 2026 年 9 月 1 日至 2027 年 8 月 31 日",
        "table": {
            "caption": "附件 产品规格与数量清单",
            "header": ["序号", "型号", "内径(mm)", "数量(套)", "单价(元)"],
            "rows": [
                ["1", "6205-2RS", "25", "6000", "980.00"],
                ["2", "6206-2RS", "30", "4000", "1010.00"],
                ["3", "6208-2RS", "40", "2000", "1160.00"],
            ],
        },
    }


def _sales_standard() -> dict:
    d = _sales_risky()
    d["clauses"] = [
        ("第一条 产品与数量", [
            "1.1 甲方向乙方销售工业级精密轴承共计 12,000 套，型号规格见附件清单。",
            "1.2 产品技术标准执行国家标准 GB/T 307.1-2017。",
        ]),
        ("第二条 合同金额与结算", [
            "2.1 本合同总金额为人民币壹仟贰佰万元整（￥12,000,000.00）。",
            "2.2 乙方应于每月 25 日前完成当月对账，并在次月 15 日前支付当月货款。",
            "2.3 乙方逾期付款的，每逾期一日按未付款项的万分之一向甲方支付滞纳金，"
            "累计不超过逾期金额的 20%，双方对等适用。",
        ]),
        ("第三条 交付与验收", [
            "3.1 甲方应于收到订单后二十日内完成发货，运输费用由甲方承担。",
            "3.2 乙方应在收货后七日内完成验收，验收标准执行 GB/T 307.1-2017；"
            "乙方逾期未提出书面异议的，视为验收合格。",
        ]),
        ("第四条 违约责任", [
            "4.1 任一方违约的，违约金按未履行部分金额每日万分之一计算，"
            "累计不超过该部分金额的 20%，双方对等适用。",
            "4.2 因产品质量问题给对方造成损失的，赔偿责任以该批次货物金额为上限，"
            "双方对等适用。",
        ]),
        ("第五条 保密义务", [
            "5.1 双方对履约过程中知悉的对方商业信息承担保密义务，保密期限为合同终止后三年。",
        ]),
        ("第六条 数据安全", [
            "6.1 双方在履约中处理对方数据应遵守《数据安全法》规定，不得擅自向第三方提供。",
        ]),
        ("第七条 不可抗力", [
            "7.1 因不可抗力不能履行合同的，遭受方应在事件发生后五日内书面通知对方"
            "并提供有效证明，否则不得主张免责。",
        ]),
        ("第八条 争议解决", [
            "8.1 因本合同产生的争议，协商不成的提交甲方所在地有管辖权的人民法院诉讼解决。",
        ]),
        ("第九条 其他", [
            "9.1 本合同自双方盖章之日起生效，有效期一年。有效期届满前三十日，"
            "任何一方未向对方发出书面续约意向的，本合同于期限届满之日终止。",
        ]),
    ]
    return d


# ======================================================================
# 写出文件
# ======================================================================
def write_docx(data: dict, path: Path, *, empty: bool = False) -> None:
    """用 python-docx 生成合同。"""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)

    if empty:
        # 只放一个空段落：解析后正文为空，触发 blocked
        doc.add_paragraph("")
        doc.save(str(path))
        print(f"  + {path.name}  (空正文)")
        return

    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = h.add_run(data["title"])
    run.bold = True
    run.font.size = Pt(18)

    a, b = data["parties"]
    meta = doc.add_paragraph()
    meta.add_run(f"{a}\n{b}\n合同编号：{data['contract_no']}\n"
                 f"统一社会信用代码：{data['uscc']}\n履行期限：{data['period']}")
    doc.add_paragraph("")

    for heading, body in data["clauses"]:
        p = doc.add_paragraph()
        r = p.add_run(heading)
        r.bold = True
        r.font.size = Pt(12)
        for line in body:
            doc.add_paragraph(line)

    tbl = data.get("table")
    if tbl:
        doc.add_paragraph("")
        cap = doc.add_paragraph()
        cap.add_run(tbl["caption"]).bold = True
        table = doc.add_table(rows=1, cols=len(tbl["header"]))
        table.style = "Table Grid"
        for i, htxt in enumerate(tbl["header"]):
            table.rows[0].cells[i].text = htxt
        for row in tbl["rows"]:
            cells = table.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = v

    doc.save(str(path))
    print(f"  + {path.name}")


def write_pdf(data: dict, path: Path) -> None:
    """用 PyMuPDF 生成带分页的 PDF（内置中文字体 china-s）。"""
    font = "china-s"
    fs_body, fs_head, fs_title = 10.5, 12.5, 20.0
    width, height = 595.0, 842.0  # A4 点
    margin = 64.0
    line_h = fs_body * 1.65
    page = None
    y = 0.0
    doc = pymupdf.open()

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=width, height=height)
        y = margin

    def need(space: float) -> None:
        if page is None or y + space > height - margin:
            new_page()

    def put(text: str, size: float, *, bold_offset: float = 0.0, center: bool = False) -> None:
        nonlocal y
        need(size * 1.65)
        w = pymupdf.get_text_length(text, fontname=font, fontsize=size)
        x = (width - w) / 2 if center else margin + bold_offset
        page.insert_text((x, y + size), text, fontname=font, fontsize=size, color=(0, 0, 0))
        y += size * 1.65

    def put_para(text: str) -> None:
        """按可用宽度折行，中文按字符切。"""
        nonlocal y
        avail = width - margin * 2
        line = ""
        for ch in text:
            trial = line + ch
            if pymupdf.get_text_length(trial, fontname=font, fontsize=fs_body) > avail:
                put(line, fs_body)
                line = ch
            else:
                line = trial
        if line:
            put(line, fs_body)
        y += 4

    new_page()
    put(data["title"], fs_title, center=True)
    y += 8
    a, b = data["parties"]
    for t in (a, b, f"合同编号：{data['contract_no']}",
              f"统一社会信用代码：{data['uscc']}", f"履行期限：{data['period']}"):
        put(t, fs_body)
    y += 8

    for heading, body in data["clauses"]:
        need(fs_head * 3)
        put(heading, fs_head)
        for line in body:
            put_para(line)
        y += 2

    tbl = data.get("table")
    if tbl:
        put(tbl["caption"], fs_head)
        cols = len(tbl["header"])
        col_w = (width - margin * 2) / cols
        left, right = margin, width - margin
        rows = [tbl["header"], *tbl["rows"]]
        top = y
        # 先算行高并画横线，再填内容，最后补竖线 —— 有完整网格，PyMuPDF 才能识别成表格
        for row in rows:
            need(line_h * 2)
            y += line_h
            page.draw_line(pymupdf.Point(left, y - line_h * 0.35),
                           pymupdf.Point(right, y - line_h * 0.35))
        bottom = y - line_h * 0.35
        for i in range(cols + 1):
            x = left + i * col_w
            page.draw_line(pymupdf.Point(x, top), pymupdf.Point(x, bottom))

        ty = top + fs_body
        for row in rows:
            x = left
            for cell in row:
                page.insert_text((x + 3, ty), str(cell),
                                 fontname=font, fontsize=fs_body - 1)
                x += col_w
            ty += line_h
        y = bottom + 6

    doc.save(str(path))
    doc.close()
    print(f"  + {path.name}")


def write_scan(src_pdf: Path, path: Path, *, dpi: int) -> None:
    """把 PDF 首页渲染成图片，模拟扫描件。dpi 很低时即为「严重模糊」。"""
    from PIL import Image, ImageFilter

    doc = pymupdf.open(str(src_pdf))
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if dpi < 100:
        # 低分辨率 + 模糊，制造真实的「扫描质量不合格」
        img = img.resize((max(pix.width // 4, 80), max(pix.height // 4, 110)))
        img = img.filter(ImageFilter.GaussianBlur(radius=1.6))
    img.save(str(path))
    doc.close()
    print(f"  + {path.name}  ({img.width}×{img.height}, dpi={dpi})")


def write_fake_encrypted(path: Path) -> None:
    """模拟加密文档：真实 OLE 复合文档头 + 一段看起来像加密数据的内容。

    真实的加密 docx 正是以这个文件头开头的，所以解析器的检测逻辑能得到真实检验。
    之所以不生成真加密文件：在 Windows 上离线加密一个 OOXML 包需要 Word 或
    msoffcrypto-tool 的加密能力，本项目不引入这个依赖。
    """
    payload = OLE_MAGIC + b"\x00" * 16 + (
        b"EncryptedPackage\x00\x00\x00\x00"
        + bytes(range(256)) * 40
    )
    path.write_bytes(payload + b"\x00" * 2048)
    print(f"  + {path.name}  (模拟 OLE 加密容器，{path.stat().st_size} bytes)")


# ======================================================================
def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{SAMPLES}")

    purchase_risky = _software_purchase_risky()
    purchase_std = _software_purchase_standard()
    sales_risky = _sales_risky()
    sales_std = _sales_standard()

    # 1. 采购合同（DOCX）
    write_docx(purchase_risky, SAMPLES / "软件采购合同_风险版.docx")
    write_docx(purchase_std, SAMPLES / "软件采购合同_标准版.docx")

    # 2. 销售合同（PDF）
    sales_risky_pdf = SAMPLES / "大额销售合同_风险版.pdf"
    write_pdf(sales_risky, sales_risky_pdf)
    write_pdf(sales_std, SAMPLES / "大额销售合同_标准版.pdf")

    # 3. 扫描件
    write_scan(sales_risky_pdf, SAMPLES / "销售合同_扫描件.png", dpi=200)
    write_scan(sales_risky_pdf, SAMPLES / "销售合同_严重模糊.png", dpi=40)

    # 4. 异常样例
    write_fake_encrypted(SAMPLES / "加密合同_模拟.docx")
    write_docx({}, SAMPLES / "空正文合同.docx", empty=True)

    print("\n样例生成完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
