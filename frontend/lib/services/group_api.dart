import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/api_config.dart';
import '../models/group.dart';

/// 群聊 API（M2）
class GroupApi {
  final http.Client _client = http.Client();

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = '${ApiConfig.baseUrl}$path';
    if (query == null || query.isEmpty) return Uri.parse(base);
    return Uri.parse(base).replace(queryParameters: query);
  }

  /// 群聊列表。失败返回 null
  Future<List<GroupInfo>?> listGroups(String userId) async {
    try {
      final resp = await _client.get(
        _uri('/api/groups', {'user_id': userId}),
      );
      if (resp.statusCode == 200) {
        final list = jsonDecode(utf8.decode(resp.bodyBytes)) as List<dynamic>;
        return list
            .map((e) => GroupInfo.fromJson(e as Map<String, dynamic>))
            .toList();
      }
    } catch (_) {}
    return null;
  }

  /// 创建群聊。失败返回 null
  Future<GroupInfo?> createGroup({
    required String userId,
    required String name,
    required List<String> characterIds,
  }) async {
    try {
      final resp = await _client.post(
        _uri('/api/groups'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'user_id': userId,
          'name': name,
          'character_ids': characterIds,
        }),
      );
      if (resp.statusCode == 200) {
        return GroupInfo.fromJson(
          jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>,
        );
      }
    } catch (_) {}
    return null;
  }

  Future<bool> deleteGroup(String userId, String groupId) async {
    try {
      final resp = await _client.delete(
        _uri('/api/groups/$groupId', {'user_id': userId}),
      );
      if (resp.statusCode == 200) {
        final data = jsonDecode(utf8.decode(resp.bodyBytes));
        return data['success'] == true;
      }
    } catch (_) {}
    return false;
  }

  /// 群聊历史。失败返回 null
  Future<List<GroupMessage>?> history(String userId, String groupId) async {
    try {
      final resp = await _client.get(
        _uri('/api/groups/$groupId/history', {'user_id': userId}),
      );
      if (resp.statusCode == 200) {
        final data = jsonDecode(utf8.decode(resp.bodyBytes));
        final list = data['messages'] as List<dynamic>? ?? [];
        return list
            .map((e) => GroupMessage.fromJson(e as Map<String, dynamic>))
            .toList();
      }
    } catch (_) {}
    return null;
  }

  /// 结束群聊，触发按角色视角的记忆蒸馏
  Future<void> endGroup(String userId, String groupId) async {
    try {
      await _client.post(
        _uri('/api/groups/$groupId/end', {'user_id': userId}),
      );
    } catch (_) {
      // 蒸馏失败不影响使用
    }
  }

  /// 群聊发言（SSE）：成员依次流式回复
  /// replyMode=target 时只让 targetCharacterId 对应的角色回应
  Future<void> chat({
    required String userId,
    required String groupId,
    required String message,
    String replyMode = 'round_robin',
    String? targetCharacterId,
    required void Function(String characterId, String name) onStart,
    required void Function(String characterId, String token) onToken,
    required void Function(String characterId) onTurnDone,
    required void Function() onEnd,
    required void Function(String error) onError,
  }) async {
    final uri = _uri('/api/groups/$groupId/chat');
    try {
      final request = http.Request('POST', uri)
        ..headers['Content-Type'] = 'application/json'
        ..headers['Accept'] = 'text/event-stream'
        ..body = jsonEncode({
          'user_id': userId,
          'message': message,
          'reply_mode': replyMode,
          if (targetCharacterId != null)
            'target_character_id': targetCharacterId,
        });

      final response = await _client.send(request);
      if (response.statusCode != 200) {
        onError('HTTP ${response.statusCode}');
        return;
      }

      String buffer = '';
      await for (final chunk in response.stream.transform(utf8.decoder)) {
        buffer += chunk;
        while (buffer.contains('\n\n')) {
          final end = buffer.indexOf('\n\n');
          final event = buffer.substring(0, end);
          buffer = buffer.substring(end + 2);
          for (final line in event.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
              final data = jsonDecode(line.substring(6)) as Map<String, dynamic>;
              switch (data['type']) {
                case 'start':
                  onStart(
                    data['character_id'] ?? '',
                    data['name'] ?? '',
                  );
                  break;
                case 'token':
                  onToken(
                    data['character_id'] ?? '',
                    data['content'] ?? '',
                  );
                  break;
                case 'done':
                  onTurnDone(data['character_id'] ?? '');
                  break;
                case 'end':
                  onEnd();
                  break;
                case 'error':
                  onError(data['message'] ?? '未知错误');
                  break;
              }
            } catch (_) {
              // 忽略单条解析失败
            }
          }
        }
      }
    } catch (e) {
      onError(e.toString());
    }
  }

  void dispose() {
    _client.close();
  }
}
