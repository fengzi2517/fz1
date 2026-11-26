import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../config/api_config.dart';

/// 群聊 WebSocket 客户端（M2.1）
///
/// 与 SSE 事件协议一致，额外支持用户中途插话：发送新消息会打断当前轮。
/// 连接失败时调用方回退到 SSE（见 GroupProvider）。
class GroupWsClient {
  WebSocket? _socket;
  StreamSubscription<dynamic>? _sub;
  bool _connected = false;

  bool get connected => _connected;

  /// 建立连接。成功返回 true，失败返回 false（由调用方回退 SSE）
  Future<bool> connect({
    required String userId,
    required String groupId,
    required void Function(Map<String, dynamic> event) onEvent,
    required void Function(String error) onError,
    void Function()? onClosed,
  }) async {
    await close();
    try {
      final wsBase = ApiConfig.baseUrl.replaceFirst('http', 'ws');
      final url = '$wsBase/api/groups/$groupId/ws?user_id=$userId';
      final socket = await WebSocket.connect(url)
          .timeout(const Duration(seconds: 3));

      _socket = socket;
      _connected = true;
      _sub = socket.listen(
        (data) {
          try {
            final decoded = jsonDecode(data as String);
            if (decoded is Map<String, dynamic>) onEvent(decoded);
          } catch (_) {
            // 忽略单条解析失败
          }
        },
        onDone: () {
          _connected = false;
          onClosed?.call();
        },
        onError: (Object e) {
          _connected = false;
          onError(e.toString());
        },
        cancelOnError: true,
      );
      return true;
    } catch (_) {
      _connected = false;
      return false;
    }
  }

  /// 发送用户消息；replyMode=target 时只让目标角色回应
  void sendMessage({
    required String content,
    String replyMode = 'round_robin',
    String? targetCharacterId,
  }) {
    final socket = _socket;
    if (socket == null || !_connected) return;
    socket.add(jsonEncode({
      'type': 'message',
      'content': content,
      'reply_mode': replyMode,
      if (targetCharacterId != null) 'target_character_id': targetCharacterId,
    }));
  }

  Future<void> close() async {
    _connected = false;
    await _sub?.cancel();
    _sub = null;
    await _socket?.close();
    _socket = null;
  }
}
