import 'dart:convert';
import 'package:http/http.dart' as http;
import '../config/api_config.dart';
import '../models/chat_message.dart';

/// API 服务 - 处理后端通信
class ApiService {
  final http.Client _client = http.Client();

  /// 发送消息，SSE 流式接收
  /// onToken: 每收到一个 token 回调
  /// onDone: 完成回调，返回 sessionId
  /// onError: 错误回调
  Future<void> sendMessage({
    required String userId,
    required String characterId,
    String? sessionId,
    required String message,
    String? model,
    double? temperature,
    required Function(String) onToken,
    required Function(String) onDone,
    required Function(String) onError,
  }) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}${ApiConfig.chatSend}');
    final body = jsonEncode({
      'user_id': userId,
      'character_id': characterId,
      if (sessionId != null) 'session_id': sessionId,
      'message': message,
      if (model != null) 'model': model,
      if (temperature != null) 'temperature': temperature,
    });

    try {
      final request = http.Request('POST', uri)
        ..headers['Content-Type'] = 'application/json'
        ..headers['Accept'] = 'text/event-stream'
        ..body = body;

      final response = await _client.send(request);

      if (response.statusCode != 200) {
        onError('HTTP ${response.statusCode}');
        return;
      }

      // 解析 SSE 流
      String buffer = '';
      await for (final chunk in response.stream.transform(utf8.decoder)) {
        buffer += chunk;
        // SSE 事件以 \n\n 分隔
        while (buffer.contains('\n\n')) {
          final eventEnd = buffer.indexOf('\n\n');
          final event = buffer.substring(0, eventEnd);
          buffer = buffer.substring(eventEnd + 2);

          // 解析 data: 行
          for (final line in event.split('\n')) {
            if (line.startsWith('data: ')) {
              final data = line.substring(6);
              try {
                final parsed = jsonDecode(data);
                final type = parsed['type'];
                if (type == 'token') {
                  onToken(parsed['content'] ?? '');
                } else if (type == 'done') {
                  onDone(parsed['session_id'] ?? '');
                } else if (type == 'error') {
                  onError(parsed['message'] ?? '未知错误');
                }
              } catch (e) {
                // 忽略解析错误
              }
            }
          }
        }
      }
    } catch (e) {
      onError(e.toString());
    }
  }

  /// 获取会话历史（不传 sessionId 则取最近一次会话）。失败返回 null
  Future<SessionHistory?> fetchHistory({
    required String userId,
    required String characterId,
    String? sessionId,
  }) async {
    final query = <String>['user_id=$userId', 'character_id=$characterId'];
    if (sessionId != null) query.add('session_id=$sessionId');
    final uri = Uri.parse(
      '${ApiConfig.baseUrl}${ApiConfig.chatHistory}?${query.join('&')}',
    );

    try {
      final response = await _client.get(uri);
      if (response.statusCode == 200) {
        return SessionHistory.fromJson(
          jsonDecode(utf8.decode(response.bodyBytes)),
        );
      }
    } catch (e) {
      // 忽略，按无历史处理
    }
    return null;
  }

  /// 结束会话
  Future<void> endSession({
    required String userId,
    required String characterId,
    required String sessionId,
  }) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}${ApiConfig.chatEnd}');
    final body = jsonEncode({
      'user_id': userId,
      'character_id': characterId,
      'session_id': sessionId,
    });

    try {
      await _client.post(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: body,
      );
    } catch (e) {
      // 结束会话失败不影响用户
    }
  }

  /// 创建角色
  Future<Character?> createCharacter({
    required String userId,
    required String name,
    required String rawContent,
  }) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}${ApiConfig.characters}');
    final body = jsonEncode({
      'user_id': userId,
      'name': name,
      'raw_content': rawContent,
    });

    try {
      final response = await _client.post(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: body,
      );
      if (response.statusCode == 200) {
        return Character.fromJson(jsonDecode(utf8.decode(response.bodyBytes)));
      }
    } catch (e) {
      // 错误处理
    }
    return null;
  }

  /// 获取角色详情（含原始人设正文）。失败返回 null
  Future<Character?> getCharacter({
    required String userId,
    required String characterId,
  }) async {
    final uri = Uri.parse(
      '${ApiConfig.baseUrl}${ApiConfig.characters}/$characterId?user_id=$userId',
    );
    try {
      final response = await _client.get(uri);
      if (response.statusCode == 200) {
        return Character.fromJson(jsonDecode(utf8.decode(response.bodyBytes)));
      }
    } catch (e) {
      // 按失败处理
    }
    return null;
  }

  /// 更新角色人设。失败返回 null
  Future<Character?> updateCharacter({
    required String userId,
    required String characterId,
    required String name,
    required String rawContent,
  }) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}${ApiConfig.characters}/$characterId');
    final body = jsonEncode({
      'user_id': userId,
      'name': name,
      'raw_content': rawContent,
    });
    try {
      final response = await _client.put(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: body,
      );
      if (response.statusCode == 200) {
        return Character.fromJson(jsonDecode(utf8.decode(response.bodyBytes)));
      }
    } catch (e) {
      // 按失败处理
    }
    return null;
  }

  /// 删除角色
  Future<bool> deleteCharacter({
    required String userId,
    required String characterId,
  }) async {
    final uri = Uri.parse(
      '${ApiConfig.baseUrl}${ApiConfig.characters}/$characterId?user_id=$userId',
    );
    try {
      final response = await _client.delete(uri);
      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes));
        return data['success'] == true;
      }
    } catch (e) {
      // 按失败处理
    }
    return false;
  }

  /// 获取角色列表。失败返回 null（区别于「加载成功但没有角色」）
  Future<List<Character>?> listCharacters(String userId) async {
    final uri = Uri.parse(
      '${ApiConfig.baseUrl}${ApiConfig.characters}?user_id=$userId',
    );

    try {
      final response = await _client.get(uri);
      if (response.statusCode == 200) {
        final List<dynamic> data = jsonDecode(utf8.decode(response.bodyBytes));
        return data.map((e) => Character.fromJson(e)).toList();
      }
    } catch (e) {
      // 网络/解析异常统一按失败处理
    }
    return null;
  }

  void dispose() {
    _client.close();
  }
}
