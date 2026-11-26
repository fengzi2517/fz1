import 'package:flutter/foundation.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../config/api_config.dart';

/// 聊天状态管理
class ChatProvider extends ChangeNotifier {
  final ApiService _api = ApiService();

  List<ChatMessage> _messages = [];
  String? _sessionId;
  String _characterId = '';
  String _characterName = '';
  bool _isStreaming = false;
  String _error = '';
  String? _model;
  double _temperature = 0.8;

  List<ChatMessage> get messages => _messages;
  String? get sessionId => _sessionId;
  String get characterId => _characterId;
  String get characterName => _characterName;
  bool get isLoading => _isStreaming;
  bool get isStreaming => _isStreaming;
  String get error => _error;
  String? get model => _model;
  double get temperature => _temperature;

  /// 切换模型
  void setModel(String? value) {
    _model = value;
    notifyListeners();
  }

  /// 调节 temperature
  void setTemperature(double value) {
    _temperature = value;
    notifyListeners();
  }

  /// 设置当前角色（同一角色重复进入时保留会话与消息）
  void setCharacter(String id, String name) {
    if (id == _characterId && _messages.isNotEmpty) {
      _characterName = name;
      notifyListeners();
      return;
    }
    if (id == _characterId) {
      // 同一角色、本地无消息：保留 sessionId，等 loadHistory 恢复
      _characterName = name;
      notifyListeners();
      return;
    }
    _characterId = id;
    _characterName = name;
    _messages = [];
    _sessionId = null;
    notifyListeners();
  }

  /// 从后端恢复该角色最近一次会话（本地已有消息时不覆盖）
  Future<void> loadHistory() async {
    if (_characterId.isEmpty || _messages.isNotEmpty) return;
    final history = await _api.fetchHistory(
      userId: ApiConfig.defaultUserId,
      characterId: _characterId,
    );
    if (history == null) return;
    if (_messages.isNotEmpty) return;
    _sessionId = history.sessionId;
    _messages = history.messages;
    notifyListeners();
  }

  /// 发送消息
  /// [onComplete] 在本轮回复结束时回调完整文本（用于自动朗读）
  Future<void> sendMessage(String text, {void Function(String reply)? onComplete}) async {
    if (text.trim().isEmpty || _isStreaming) return;

    // 添加用户消息
    _messages.add(ChatMessage(role: 'user', content: text));
    _isStreaming = true;
    _error = '';
    notifyListeners();

    // 添加占位的 AI 消息（流式填充）
    final aiMessage = ChatMessage(role: 'assistant', content: '', isStreaming: true);
    _messages.add(aiMessage);
    notifyListeners();

    await _api.sendMessage(
      userId: ApiConfig.defaultUserId,
      characterId: _characterId,
      sessionId: _sessionId,
      message: text,
      model: _model,
      temperature: _temperature,
      onToken: (token) {
        // 流式追加到最后一条 AI 消息
        if (_messages.isNotEmpty && _messages.last.isStreaming) {
          _messages.last = _messages.last.copyWith(
            content: _messages.last.content + token,
          );
          notifyListeners();
        }
      },
      onDone: (sessionId) {
        _sessionId = sessionId;
        _isStreaming = false;
        // 标记流式结束
        if (_messages.isNotEmpty && _messages.last.isStreaming) {
          _messages.last = _messages.last.copyWith(isStreaming: false);
        }
        notifyListeners();
        final reply = _messages.isNotEmpty ? _messages.last.content : '';
        if (onComplete != null && reply.trim().isNotEmpty) {
          onComplete(reply);
        }
      },
      onError: (errorMsg) {
        _error = errorMsg;
        _isStreaming = false;
        // 移除空的 AI 消息或标记错误
        if (_messages.isNotEmpty && _messages.last.isStreaming) {
          if (_messages.last.content.isEmpty) {
            _messages.removeLast();
          } else {
            _messages.last = _messages.last.copyWith(isStreaming: false);
          }
        }
        notifyListeners();
      },
    );
  }

  /// 结束当前会话并触发记忆蒸馏（保留本地消息，便于继续查看）
  Future<void> endSession() async {
    if (_sessionId == null) return;
    await _api.endSession(
      userId: ApiConfig.defaultUserId,
      characterId: _characterId,
      sessionId: _sessionId!,
    );
  }

  /// 清空对话（不结束会话）
  void clearMessages() {
    _messages = [];
    _sessionId = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}
