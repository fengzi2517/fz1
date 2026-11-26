/// 会话时间显示：今天显示时分，昨天显示「昨天」，更早显示月-日
String formatConversationTime(DateTime? ts) {
  if (ts == null) return '';
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(ts.year, ts.month, ts.day);
  final diff = today.difference(day).inDays;
  if (diff == 0) {
    return '${ts.hour.toString().padLeft(2, '0')}:'
        '${ts.minute.toString().padLeft(2, '0')}';
  }
  if (diff == 1) return '昨天';
  if (ts.year == now.year) {
    return '${ts.month.toString().padLeft(2, '0')}-'
        '${ts.day.toString().padLeft(2, '0')}';
  }
  return '${ts.year}/${ts.month}/${ts.day}';
}
