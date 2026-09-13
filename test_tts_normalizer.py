"""
tts_normalizer 单元测试：覆盖特殊字符、数学符号/公式、上下标、分式、根号等场景。

运行：python test_tts_normalizer.py
依赖：仅标准库
"""

from tts_normalizer import NormalizeStats, normalize_for_tts


def _norm(text: str) -> str:
    return normalize_for_tts(text)


def check(name: str, src: str, expect_contains: list, expect_not_contains: list = None):
    """断言规范化结果包含/不包含指定片段。"""
    out = _norm(src)
    ok = True
    for frag in expect_contains:
        if frag not in out:
            print(f"[FAIL] {name}\n  输入: {src!r}\n  输出: {out!r}\n  期望包含: {frag!r}")
            ok = False
    for frag in (expect_not_contains or []):
        if frag in out:
            print(f"[FAIL] {name}\n  输入: {src!r}\n  输出: {out!r}\n  期望不含: {frag!r}")
            ok = False
    if ok:
        print(f"[OK]   {name}: {src!r} -> {out!r}")
    return ok


def main() -> int:
    results = []

    # --- 1. 上下标 ---
    results.append(check("平方", "圆的面积是 πr²", ["派r的平方"]))
    results.append(check("立方", "体积为 5 cm³", ["5 立方厘米"]))
    results.append(check("n次方", "2ⁿ 的增长", ["2的n次方"]))
    results.append(check("负指数", "半衰期为 10⁻³ 秒", ["10的负三次方"]))
    # 用户场景：PDF 把负号映射成 FIGURE DASH（U+2012）时的读法兜底
    results.append(check("破折号负号上标读法", "t = 10\u207b\u2074\u00b3 秒", ["10的负43次方"]))
    results.append(check("LaTeX 回退标记含破折号负号", "t = 10^{\u201243} 秒", ["10的负43次方"]))
    results.append(check("LaTeX 回退标记含 U+2212 负号", "t = 10^{\u221243} 秒", ["10的负43次方"]))
    results.append(check("LaTeX 回退标记不含 `^{`残留", "t = 10^{\u201243} 秒", ["的负43次方"], ["^{", "}"]))
    results.append(check("下标变量", "数列 a₁, a₂, …, aₙ", ["a下标1", "a下标n"]))
    results.append(check("复合指数", "x²⁵ 很大", ["x的25次方"]))
    results.append(check("括号平方", "(x+1)² 展开后", ["(x 加 1)的平方"]))
    results.append(check("ASCII幂", "E = mc^2 能量", ["mc的平方"]))
    results.append(check("花括号幂", "a^{n} 次", ["a的n次方"]))

    # --- 2. 单位 ---
    results.append(check("平方米", "房面积约 120 m²", ["120 平方米"]))
    results.append(check("平方千米", "国土面积 960万km²", ["960万平方千米"]))
    results.append(check("速率单位", "光速为 3×10⁸ m s⁻¹", ["每秒"]))
    results.append(check("兼容单位字符", "面积是 120 ㎡", ["平方米"]))

    # --- 3. 化学式 ---
    results.append(check("水", "H₂O 是生命之源", ["水是生命之源"]))
    results.append(check("二氧化碳", "排放 CO₂ 增加", ["二氧化碳"]))

    # --- 4. 分式与分数 ---
    results.append(check("分数字符½", "占 ½ 的多数", ["二分之一"]))
    results.append(check("斜杠分数", "大约 3/4 的人", ["4分之3"]))
    results.append(check("日期斜杠", "发表于 2024/09/13", ["2024年9月13日"], ["2024/09/13"]))
    results.append(check("八分之五", "⅝ 杯面粉", ["八分之五"]))

    # --- 5. 根号 ---
    results.append(check("根号数字", "√2 约等于 1.414", ["根号2"]))
    results.append(check("根号表达式", "计算 √(x+1) 的值", ["根号下x 加 1"]))
    results.append(check("立方根", "∛27 等于 3", ["立方根27 等于 3"]))

    # --- 6. 求和/积分 ---
    results.append(check("求和带界", "∑ᵢ₌₁ⁿ i 的值", ["从i等于1到n求和"]))
    results.append(check("定积分", "∫₀¹ x dx", ["从0到1积分"]))
    results.append(check("裸求和", "计算 ∑ 之后", ["求和"]))

    # --- 7. 符号读法 ---
    results.append(check("圆周率", "π 约等于 3.14", ["派约等于 3.14"]))
    results.append(check("不等号", "x ≤ 5 且 y ≥ 3", ["小于等于", "大于等于"]))
    results.append(check("约等", "c ≈ 3×10⁸", ["约等于"]))
    results.append(check("乘除号", "6 × 7 ÷ 2", ["乘以", "除以"]))
    results.append(check("正负", "误差为 ±0.5", ["正负0.5"]))
    results.append(check("无穷", "n → ∞ 时", ["无穷大"]))
    results.append(check("希腊字母", "α、β、γ 射线", ["阿尔法", "贝塔", "伽马"]))
    results.append(check("属于", "x ∈ A", ["属于"]))
    results.append(check("因为所以", "∴ 结论成立 ∵ 条件满足", ["所以", "因为"]))

    # --- 8. ASCII 运算符（数学语境）---
    results.append(check("等号加号", "y = x + 1", ["y 等于 x 加 1"]))
    results.append(check("C++不误伤", "用 C++ 编程", ["C++"]))
    results.append(check("普通加号不误伤", "10+ 条评论", ["10+"]))

    # --- 9. 百分比/角度/温度 ---
    results.append(check("百分比", "增长了 50%", ["百分之50"]))
    results.append(check("角度", "直角为 90°", ["90度"]))
    results.append(check("摄氏度", "气温 25°C", ["25摄氏度"]))
    results.append(check("摄氏度字符", "水温 25℃", ["25摄氏度"]))
    results.append(check("角分角秒", "北纬 32°15′30″", ["32度", "15分", "30秒"]))
    results.append(check("导数撇", "f′(x) 与 f″(x)", ["f撇", "f两撇"]))

    # --- 10. 特殊字符与乱码 ---
    results.append(check("列表符号", "• 第一条⁃第二条", ["第一条", "第二条"], ["•", "⁃"]))
    results.append(check("零宽字符", "你\u200b好\u200d呀", ["你好呀"], ["\u200b"]))
    results.append(check("替换字符", "乱码\ufffd测试", ["乱码测试"], ["\ufffd"]))
    results.append(check("制表符区", "┌────┐边框", ["边框"], ["┌"]))
    results.append(check("私用区", "字\uE003符", ["字符"]))
    results.append(check("带圈数字", "①②③ 依次排列", ["依次排列"], ["①"]))

    # --- 11. 语义连贯性 ---
    results.append(check("完整公式", "勾股定理：a² + b² = c²",
                          ["a的平方加 b的平方等于 c的平方"]))
    results.append(check("全角标点保留", "他说：你好，世界！", ["说：你好，世界！"]))
    results.append(check("中文语境", "这个数是 5 的平方，即 25。", ["，即 25。"]))
    results.append(check("人名中点不误伤", "史蒂夫·乔布斯创办了苹果", ["史蒂夫·乔布斯"]))

    # --- 12. 统计信息 ---
    st = NormalizeStats()
    normalize_for_tts("面积 100 m²，比例 50%，参数 α 取 √2。", st)
    print(f"\n统计示例: {st.summary()}")
    results.append(st.total_replaced >= 4 and st.total_removed >= 0)

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n===== 测试结果: {passed}/{total} 通过 =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
