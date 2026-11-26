/// 群聊数据模型（M2）
class GroupMember {
  final String characterId;
  final String name;
  final String description;

  GroupMember({
    required this.characterId,
    required this.name,
    this.description = '',
  });

  factory GroupMember.fromJson(Map<String, dynamic> json) {
    return GroupMember(
      characterId: json['character_id'] ?? '',
      name: json['name'] ?? '',
      description: json['description'] ?? '',
    );
  }
}

class GroupInfo {
  final String groupId;
  final String name;
  final List<GroupMember> members;

  GroupInfo({
    required this.groupId,
    required this.name,
    required this.members,
  });

  factory GroupInfo.fromJson(Map<String, dynamic> json) {
    final members = (json['members'] as List<dynamic>? ?? [])
        .map((e) => GroupMember.fromJson(e as Map<String, dynamic>))
        .toList();
    return GroupInfo(
      groupId: json['group_id'] ?? '',
      name: json['name'] ?? '',
      members: members,
    );
  }
}

class GroupMessage {
  final String role; // user / assistant
  final String content;
  final String characterId;
  final String name;
  final bool isStreaming;

  GroupMessage({
    required this.role,
    required this.content,
    this.characterId = '',
    this.name = '',
    this.isStreaming = false,
  });

  bool get isUser => role == 'user';

  factory GroupMessage.fromJson(Map<String, dynamic> json) {
    return GroupMessage(
      role: json['role'] ?? '',
      content: json['content'] ?? '',
      characterId: json['character_id'] ?? '',
      name: json['name'] ?? '',
    );
  }

  GroupMessage copyWith({String? content, bool? isStreaming}) {
    return GroupMessage(
      role: role,
      content: content ?? this.content,
      characterId: characterId,
      name: name,
      isStreaming: isStreaming ?? this.isStreaming,
    );
  }
}
