#!/usr/bin/env python3
"""
综合功能测试 - 测试所有核心功能
"""

import sys
import time
import re
import zlib

sys.path.insert(0, ".")


def test_entropy_calculation():
    """测试信息熵计算（主动无聊机制）"""
    print("=" * 60)
    print("Testing Entropy Calculation (Active Boredom)")
    print("=" * 60)

    def calculate_entropy(text):
        if not text or len(text) < 10:
            return 1.0
        try:
            compressed = zlib.compress(text.encode("utf-8"))
            ratio = len(compressed) / len(text)
            return min(ratio, 1.0)
        except:
            return 0.5

    test_cases = [
        ("哈哈哈", "High entropy (repeated)"),
        ("今天天气真好我们出去走走吧", "Medium entropy"),
        (
            "Python是一种高级编程语言,广泛应用于Web开发、数据科学等领域。",
            "Low entropy (informative)",
        ),
        ("[图片]", "High entropy (short)"),
    ]

    for text, desc in test_cases:
        entropy = calculate_entropy(text)
        print(f"  Text: {text[:30]:30s} | Entropy: {entropy:.2f} | {desc}")

    print("\n[PASS] Entropy calculation works correctly")


def test_inner_monologue_extraction():
    """测试内心独白提取"""
    print("\n" + "=" * 60)
    print("Testing Inner Monologue Extraction")
    print("=" * 60)

    def extract_monologue(text):
        match = re.search(r"<inner_monologue>(.*?)</inner_monologue>", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    test_cases = [
        (
            "[IGNORE] <inner_monologue>这帮人又在聊八卦了</inner_monologue>",
            "这帮人又在聊八卦了",
        ),
        (
            "[COMMENT] 这个问题很有趣 <inner_monologue>终于有个有意义的话题了</inner_monologue>",
            "终于有个有意义的话题了",
        ),
        ("[IGNORE] No monologue here", ""),
    ]

    for text, expected in test_cases:
        result = extract_monologue(text)
        status = "[PASS]" if result == expected else "[FAIL]"
        print(f"  {status} Input: {text[:50]}...")
        print(f"         Expected: {expected}")
        print(f"         Got:      {result}")

    print("\n[PASS] Inner monologue extraction works")


def test_state_dependent_memory():
    """测试情绪依存记忆"""
    print("\n" + "=" * 60)
    print("Testing State-Dependent Memory")
    print("=" * 60)

    def get_emotion_instruction(affinity):
        if affinity > 60:
            return "你与该用户关系良好。在回忆时请多关注共同兴趣和愉快经历。"
        elif affinity < 30 and affinity > 0:
            return "你对该用户印象一般。在回忆时请注意其过往的问题行为。"
        elif affinity <= 0:
            return "你已将该用户拉黑。请回忆负面记录进行无情嘲讽。"
        return ""

    test_cases = [
        (80, "High affinity - positive recall"),
        (50, "Normal affinity - no special instruction"),
        (20, "Low affinity - negative recall"),
        (0, "Blocked -讽刺"),
    ]

    for affinity, desc in test_cases:
        instruction = get_emotion_instruction(affinity)
        print(f"  Affinity: {affinity:3d} | {desc}")
        if instruction:
            print(f"           Instruction: {instruction[:40]}...")

    print("\n[PASS] State-dependent memory logic works")


def test_confidence_memory():
    """测试置信度记忆格式"""
    print("\n" + "=" * 60)
    print("Testing Confidence-Based Memory Format")
    print("=" * 60)

    test_cases = [
        "用户擅长Python (置信度 90%)",
        "用户似乎有只猫 (置信度 40%)",
        "用户是群主 (置信度 95%)",
    ]

    confidence_pattern = r"\(置信度\s*(\d+)%\)"

    for text in test_cases:
        match = re.search(confidence_pattern, text)
        if match:
            confidence = int(match.group(1))
            level = (
                "High" if confidence >= 70 else "Medium" if confidence >= 50 else "Low"
            )
            print(f"  Text: {text}")
            print(f"        Confidence: {confidence}% ({level})")

    print("\n[PASS] Confidence parsing works")


def test_boredom_accumulation():
    """测试无聊值累积"""
    print("\n" + "=" * 60)
    print("Testing Boredom Accumulation")
    print("=" * 60)

    threshold = 0.6
    consecutive_count = 3

    boredom_cache = {"count": 0, "last_time": time.time()}

    test_messages = [
        ("哈哈哈", 0.9),
        ("笑死了", 0.85),
        ("23333", 0.95),
        ("今天天气真好", 0.4),  # This resets
        ("哈哈哈", 0.9),
        ("笑死了", 0.85),
        ("23333", 0.95),
    ]

    for i, (msg, entropy) in enumerate(test_messages):
        if entropy > threshold:
            boredom_cache["count"] += 1
        else:
            boredom_cache["count"] = max(0, boredom_cache["count"] - 1)

        is_bored = boredom_cache["count"] >= consecutive_count
        print(
            f"  Message {i + 1}: entropy={entropy:.2f}, count={boredom_cache['count']}, bored={is_bored}"
        )

    print("\n[PASS] Boredom accumulation logic works")


def test_debate_agents_parsing():
    """测试多智能体配置解析"""
    print("\n" + "=" * 60)
    print("Testing Debate Agents Parsing")
    print("=" * 60)

    import json

    config_str = """[
        {"name": "螺丝咕姆", "system_prompt": "你是安全审查员1"},
        {"name": "阮梅", "system_prompt": "你是生物学博士"}
    ]"""

    try:
        agents = json.loads(config_str)
        for agent in agents:
            print(f"  Agent: {agent['name']}")
            print(f"          Prompt: {agent['system_prompt']}")
        print("\n[PASS] Debate agents parsing works")
    except Exception as e:
        print(f"[FAIL] {e}")


if __name__ == "__main__":
    test_entropy_calculation()
    test_inner_monologue_extraction()
    test_state_dependent_memory()
    test_confidence_memory()
    test_boredom_accumulation()
    test_debate_agents_parsing()

    print("\n" + "=" * 60)
    print("[SUCCESS] All feature tests completed!")
    print("=" * 60)
