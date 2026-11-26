/// 会话列表项（角色单聊 / 群聊）
class ConversationItem {
  final String type; // character / group
  final String id;
  final String name;
  final String preview;
  final DateTime? timestamp;
  final List<String> members;

  ConversationItem({
    required this.type,
    required this.id,
    required this.name,
    this.preview = '',
    this.timestamp,
    this.members = const [],
  });

  bool get isGroup => type == 'group';

  factory ConversationItem.fromJson(Map<String, dynamic> json) {
    final ts = json['timestamp'];
    return ConversationItem(
      type: json['type'] ?? 'character',
      id: json['id'] ?? '',
      name: json['name'] ?? '',
      preview: json['preview'] ?? '',
      timestamp: ts is String ? DateTime.tryParse(ts)?.toLocal() : null,
      members: (json['members'] as List<dynamic>? ?? [])
          .map((e) => e.toString())
          .toList(),
    );
  }
}
