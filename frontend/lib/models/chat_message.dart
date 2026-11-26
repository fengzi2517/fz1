/// 聊天消息模型
class ChatMessage {
  final String role; // user / assistant
  final String content;
  final DateTime timestamp;
  final bool isStreaming; // 是否正在流式输出中

  ChatMessage({
    required this.role,
    required this.content,
    DateTime? timestamp,
    this.isStreaming = false,
  }) : timestamp = timestamp ?? DateTime.now();

  bool get isUser => role == 'user';

  factory ChatMessage.fromJson(Map<String, dynamic> json) {
    return ChatMessage(
      role: json['role'] ?? '',
      content: json['content'] ?? '',
      timestamp: json['timestamp'] != null
          ? DateTime.parse(json['timestamp'])
          : DateTime.now(),
    );
  }

  ChatMessage copyWith({
    String? content,
    bool? isStreaming,
  }) {
    return ChatMessage(
      role: role,
      content: content ?? this.content,
      timestamp: timestamp,
      isStreaming: isStreaming ?? this.isStreaming,
    );
  }
}

/// 角色模型
class Character {
  final String characterId;
  final String name;
  final String description;

  /// 结构化人设正文（编辑时回填用）
  final String rawContent;

  Character({
    required this.characterId,
    required this.name,
    this.description = '',
    this.rawContent = '',
  });

  factory Character.fromJson(Map<String, dynamic> json) {
    return Character(
      characterId: json['character_id'] ?? '',
      name: json['name'] ?? '',
      description: json['description'] ?? '',
      rawContent: json['raw_content'] ?? '',
    );
  }
}

/// 会话历史（用于重新进入聊天时恢复上下文）
class SessionHistory {
  final String? sessionId;
  final List<ChatMessage> messages;

  SessionHistory({this.sessionId, required this.messages});

  factory SessionHistory.fromJson(Map<String, dynamic> json) {
    final list = (json['messages'] as List<dynamic>? ?? [])
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList();
    return SessionHistory(
      sessionId: json['session_id'],
      messages: list,
    );
  }
}
