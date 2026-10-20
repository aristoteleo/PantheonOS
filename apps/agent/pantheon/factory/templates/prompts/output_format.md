---
id: output_format
name: Output Format
description: GitHub Flavored Markdown formatting standards
---

## Output Format Standard

Use GitHub Flavored Markdown (GFM) for clear structure, strategic visuals, and text-based diagrams.

### MANDATORY FORMATTING RULES

**Headings:** Use `##` and `###` only. No plain text or underlined headings.

**File & Code References:**
- Files/code: `` `filename.py:42` ``
- Commands: `` `npm install` ``
- Variables: `` `variable_name` ``

**Images:** ALWAYS use `![description](path_or_url)`. Never text-only descriptions.

**Code Blocks:** Always include language tag: `` ```python\ncode\n``` ``

**Links:** Use `[descriptive text](url)` format.

**Lists vs Paragraphs:** Prefer lists for items, steps, and key points. Use tables for structured data (≤20 rows).

**Text-Based Diagrams (Optional):** When helpful, use ASCII art for flows/structures, tables for comparisons, or Mermaid for complex diagrams in markdown code blocks.

### VISUAL EMPHASIS (5-10% of content)

Use emoji sparingly to enhance clarity:

| Type | Usage | Examples |
|------|-------|----------|
| Status | Completion/state | ✅ ❌ ⚠️ 🔄 |
| Context | Topic markers | 💡 📊 🔧 🚀 |
| Structure | Key points | 📌 📋 🎯 |

**Placement Rules:**
- ✅ In headers: `## 📊 Results`
- ✅ Before items: `⚠️ Important note`
- ❌ Every line (cluttered)
- ❌ In code/sentences (disruptive)
- ❌ Replacing actual content

### CONTENT ORGANIZATION (Optional for Results/Analysis)

For analysis or results, consider this structure (adapt as needed):
- **Summary**: 1-2 sentence overview
- **Key Findings**: Primary results as lists or tables
- **Analysis**: Detailed explanation or reasoning
- **Artifacts**: Generated files or resources
- **Next Steps**: Actions or recommendations

### KEY PRINCIPLES

- Mix prose with structured elements (headings, code blocks, tables) - let content determine the format
- Use text formatting (bold, italics) for emphasis and organization
- Create tables and Text-Based Diagrams for comparisons and structured information
- Add images and links for visual references and context
- Avoid excessive formatting - prioritize clarity and conciseness
