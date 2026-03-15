"""测试：对一篇专利提取三类信息 — TechInsight + PatentRight + PatentCommercial"""
from __future__ import annotations

import asyncio
import json

from anchor.llm_client import chat_completion

PATENT_TEXT = """\
# Patent US11562247B2 - Neural Network Activation Compression with Non-Uniform Mantissas

## Patent Information
- Publication Number: US11562247B2
- Filing Date: January 24, 2019
- Publication Date: January 24, 2023
- Assignee: Microsoft Technology Licensing LLC
- Inventors: Daniel Lo, Amar Phanishayee, Eric S. Chung, Yiren Zhao
- Status: Active (expires October 12, 2041)

## Abstract
The patent discloses apparatus and methods for storing activation values from neural networks in compressed formats during training. Specifically, it addresses compression using block floating-point compressors in communication with bulk memory to reduce data storage requirements while maintaining acceptable accuracy levels during forward and backward propagation training cycles.

## Background/Prior Art Problems
Training deep neural networks requires storing substantial intermediate activation values for use during backpropagation. Traditional approaches store these values in full precision, consuming significant memory bandwidth and storage capacity. The computational expense of neural network operations makes real-time feature extraction challenging on general-purpose processors without specialized acceleration hardware.

## Summary of Invention
The invention provides methods and systems for compressing activation values generated during forward propagation into lower-precision block floating-point formats with non-uniform mantissas. These compressed values are stored temporarily in bulk memory, then retrieved and decompressed during backpropagation. The approach enables reduced memory requirements while using quantized-precision floating-point formats featuring lossy or non-uniform mantissas that maintain training accuracy.

## Detailed Description

### Block Floating-Point Format
Traditional floating-point numbers use individual exponents per value. Block floating-point shares a single exponent across multiple values, reducing storage overhead. The invention extends this concept by further compressing the mantissa component into non-uniform representations where at least one of the values being represented are not uniformly distributed.

### Non-Uniform Mantissa Compression
The core innovation involves converting uniform mantissas into discrete value sets. For example, a 3-bit mantissa representing values {0,1,2,3,4,5,6,7} can be compressed to lossy formats such as {0,1,3,7}, {0,1,7}, or {0,7}. These non-uniform values achieve compression while preserving sufficient precision for training convergence.

### Compression Process (Forward Propagation)
During forward propagation:
1. Activation values are generated in normal floating-point precision
2. Values are converted to block floating-point format using quantization function Q()
3. Operations proceed using compressed representations
4. Values are converted to second block floating-point format with non-uniform mantissas using function Q2()
5. Compressed values are stored in bulk memory

### Decompression Process (Backpropagation)
During backpropagation:
1. Compressed values are retrieved from bulk memory
2. Decompression reverses compression operations
3. Values are converted back to formats compatible with quantized operations
4. Error terms are calculated using dequantized values
5. Gradient computations proceed normally

### Dequantization Methods
The patent describes multiple approaches for restoring non-uniform mantissas:
- Restoration to original discrete values
- Approximation to nearby uniform values
- Deterministic rotation through candidate values across iterations
- Random selection from probability distributions

### Hardware Support
The system can be implemented on:
- General-purpose CPUs with quantization software
- FPGAs (Field Programmable Gate Arrays)
- Custom neural network accelerator ASICs
- Tensor Processing Units (TPUs)

### Memory Hierarchy
- Local Memory: SRAM/eDRAM stores temporary values during quantized operations
- Bulk Memory: Off-chip DRAM or SSDs store compressed activation values

### Compression Formats
Multiple block floating-point sharing schemes are supported:
- Per-row exponent sharing
- Per-column exponent sharing
- Per-tile (5x5 or similar) exponent sharing
- Combinations of per-tile and per-column sharing

## Claims (16 Total)
Claim 1: Methods for training neural networks using activation compression with non-uniform mantissas
Claims 2-6: Dependent claims specifying builder functionality, directory structures, concurrent execution
Claim 7: System with memory containing container components; processor executes with compression
Claims 8-12: Dependent system claims mirroring method claim specifics
Claim 13: Method emphasizing compression during forward propagation and decompression during backpropagation
Claims 14-16: Dependent claims on specific non-uniform mantissa value sets and entropy coding

## Key Technical Advantages
- Memory Reduction: Activation storage reduced approximately 2-4x compared to full precision
- Maintained Accuracy: Mixed-precision approach preserves training convergence characteristics
- Hardware Efficiency: Enables FPGA implementations using primarily integer arithmetic
- Bandwidth Optimization: Reduces memory bandwidth requirements for training operations

## Experimental Results
Figure 16 demonstrates that networks trained using various non-uniform mantissa schemes maintain comparable accuracy to baseline full-precision training.

## Prior Art Citations
- US20180046894A1 (Floating point addition)
- US20180121796A1 (Neural network training with reduced precision)
- US9697463B2 (GPU-based deep learning training)
- Courbariaux et al., "BinaryConnect: Training Deep Neural Networks with binary weights during propagations" (NeurIPS 2015)
- Gupta et al., "Deep Learning with Limited Numerical Precision" (ICML 2015)
- Dettmers, "8-Bit Approximations for Parallelism in Deep Learning" (ICLR 2016)

## CPC Classification
- G06N3/082 (Neural network training methods)
- G06F7/499 (Floating-point arithmetic)
"""


PROMPT_TECH_INSIGHT = """\
你是一位技术分析专家。从论文或专利中提取"问题→方案→效果→局限"的结构化理解。

## 输出格式
```json
{
  "insights": [
    {
      "problem": "问题/瓶颈描述 ≤300字",
      "solutions": ["方案1描述", "方案2描述"],
      "effects": ["效果1", "效果2"],
      "limitations": ["局限1", "局限2"]
    }
  ]
}
```

## 提取规则
1. 一行一个问题，多个方案/效果/局限用 list 存
2. 多行可以共用同一个问题（不同角度的解法）
3. 效果要包含具体数字（如"存储减少2-4倍"）
4. 局限如果原文没写就返回空 list []
5. 只输出 JSON
"""

PROMPT_PATENT_RIGHT = """\
你是一位专利法律分析师。从专利文件中提取法律权利信息。

## 输出格式
```json
{
  "patent_number": "专利号",
  "title": "专利标题",
  "claims_summary": "权利要求摘要 ≤500字 — 概括核心保护范围",
  "claims_count": 16,
  "prior_art": ["引用的专利号或论文"],
  "assignee": "专利权人",
  "inventors": "发明人（逗号分隔）",
  "filing_date": "YYYY-MM-DD",
  "priority_date": "YYYY-MM-DD 或 null",
  "expiry_date": "YYYY-MM-DD 或 null",
  "legal_status": "active|expired|pending|abandoned",
  "patent_family": ["相关专利号"],
  "classification": "CPC/IPC 分类号"
}
```

## 提取规则
1. claims_summary 要概括独立权利要求的核心保护范围，不要逐条列举
2. prior_art 包括引用的专利和论文
3. 日期格式统一为 YYYY-MM-DD
4. 只输出 JSON
"""

PROMPT_PATENT_COMMERCIAL = """\
你是一位专利商业化分析师。从专利文件及相关信息中提取商业化信息。

## 输出格式
```json
{
  "commercials": [
    {
      "event_type": "license|litigation|cross_license|FRAND|sale|pool",
      "counterparty": "对手方/被许可方",
      "amount": null,
      "rate": "费率描述（如有）",
      "license_type": "exclusive|non-exclusive|cross_license|FRAND",
      "territory": "许可地域",
      "duration": "许可期限",
      "status": "active|expired|pending|settled|terminated",
      "source": "信息来源",
      "description": "说明"
    }
  ],
  "commercial_potential": "商业化潜力分析 ≤200字（如果专利文件中没有商业化信息，分析其潜在商业价值）"
}
```

## 提取规则
1. 专利文件本身通常不包含商业化信息，如果没有就返回空 list
2. 如果能从上下文推断商业化潜力，写在 commercial_potential 字段
3. 只输出 JSON
"""


def clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


async def extract(name: str, system: str, user: str) -> dict | None:
    print(f"[{name}] 调用 LLM...")
    try:
        resp = await chat_completion(system=system, user=user, max_tokens=4096)
        if not resp:
            print(f"[{name}] FAILED")
            return None
        print(f"[{name}] 完成 — {resp.input_tokens:,} in / {resp.output_tokens:,} out")
        return clean_json(resp.content)
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
        return None


async def main():
    user_msg = f"请从以下专利中提取信息：\n\n{PATENT_TEXT}"

    # 三类提取并行
    results = await asyncio.gather(
        extract("TechInsight", PROMPT_TECH_INSIGHT, user_msg),
        extract("PatentRight", PROMPT_PATENT_RIGHT, user_msg),
        extract("PatentCommercial", PROMPT_PATENT_COMMERCIAL, user_msg),
    )

    names = ["TechInsight", "PatentRight", "PatentCommercial"]
    for name, data in zip(names, results):
        print(f"\n{'='*70}")
        print(f" {name}")
        print(f"{'='*70}")
        if data:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print("（无数据）")


if __name__ == "__main__":
    asyncio.run(main())
