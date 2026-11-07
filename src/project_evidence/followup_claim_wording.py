"""Planning-only presentation advice; not validation, citation selection or repair."""

CLAIM_WORDING_NOTICE = ('规划表达建议（不改变证据标准）：claim_summary用具体方法、条件、数据和结果写出一个可核对的结论，'
          '不要把源码位置本身当作结论，也不为装饰定位而重复整段行号。通常将位置坐标保留在本块'
          'source_lines和evidence_plan中；这不是禁止行号。题目要求定位、行号本身是必要事实时，仍明确报告，'
          '且每个提及的行都必须由同一块选中。保留业务数值、阈值、版本、错误码和代码字面量，'
          '不得靠删数字、删必要事实或扩大引用范围逃避核对。所选源码仍须完整支持条件、调用、结果等全部结论；'
          '坐标一致不代表语义正确。宿主不删文字、不猜行、不补引用或重试；缺必要证据仍按原合同补查、gap或clarify。')
