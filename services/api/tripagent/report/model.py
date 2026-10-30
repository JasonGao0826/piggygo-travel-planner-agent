"""报告的内容模型：先把要写的内容整理成章节、表格、卡片，再交给 Word 渲染。

内容和排版分开，测试可以直接检查内容（例如同一个时间在所有章节里是否一致），不用解析 Word 文件。
"""

from dataclasses import dataclass, field


@dataclass
class Paragraph:
    text: str
    muted: bool = False


@dataclass
class Bullets:
    items: list[str]


@dataclass
class Row:
    cells: list[str]
    fill: str | None = None   # 行底色，六位十六进制，例如 "DCEBFA"
    bold: bool = False


@dataclass
class Table:
    columns: list[str]
    rows: list[Row]
    widths_mm: list[float] | None = None


@dataclass
class Link:
    label: str
    url: str


@dataclass
class Card:
    """项目详情卡片（和 V3 报告里的项目介绍一样）。"""

    title: str
    facts: list[tuple[str, str]]          # （标签，内容），例如（"地址"，"53 Brecon Street"）
    links: list[Link] = field(default_factory=list)
    intro: str | None = None
    fill: str | None = None


@dataclass
class Heading:
    text: str
    fill: str | None = None


Block = Paragraph | Bullets | Table | Card | Heading


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)
    new_page: bool = False   # 篇幅大的章节另起一页


@dataclass
class Report:
    title: str
    cover_lines: list[str]
    sections: list[Section]
    legend: list[tuple[str, str]] = field(default_factory=list)  # 地点配色图例：（地点，颜色）

    def plain_text(self) -> str:
        """把所有内容拼成纯文本，测试用。"""
        parts = [self.title, *self.cover_lines]
        for s in self.sections:
            parts.append(s.title)
            for b in s.blocks:
                if isinstance(b, (Paragraph, Heading)):
                    parts.append(b.text)
                elif isinstance(b, Bullets):
                    parts.extend(b.items)
                elif isinstance(b, Table):
                    parts.append(" | ".join(b.columns))
                    parts.extend(" | ".join(r.cells) for r in b.rows)
                elif isinstance(b, Card):
                    parts.append(b.title)
                    parts.extend(f"{k}：{v}" for k, v in b.facts)
                    parts.extend(f"{l.label}：{l.url}" for l in b.links)
                    if b.intro:
                        parts.append(b.intro)
        return "\n".join(parts)
