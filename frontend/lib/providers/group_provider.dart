import 'dart:async';

import 'package:flutter/foundation.dart';

import '../config/api_config.dart';
import '../models/group.dart';
import '../services/group_api.dart';
import '../services/group_ws.dart';

/// 群聊状态管理（M2）
class GroupProvider extends ChangeNotifier {
  final GroupApi _api;
  final GroupWsClient _ws;

  GroupProvider({GroupApi? api, GroupWsClient? ws})
      : _api = api ?? GroupApi(),
        _ws = ws ?? GroupWsClient();

  List<GroupInfo> _groups = [];
  GroupInfo? _current;
  List<GroupMessage> _messages = [];
  bool _isStreaming = false;
  bool _wsConnected = false;
  bool _disposed = false;
  String _error = '';

  /// 发言控制：round_robin=自由轮询，target=只让指定角色回应
  String _replyMode = 'round_robin';
  String? _targetCharacterId;

  /// 当前正在流式输出的助手消息下标
  int? _activeIndex;

  /// 已发出但还没收到 end 的轮次数（WS 插话时会同时存在两轮）
  int _pendingRounds = 0;

  List<GroupInfo> get groups => _groups;
  GroupInfo? get current => _current;
  List<GroupMessage> get messages => _messages;
  bool get isStreaming => _isStreaming;
  String get error => _error;
  String get replyMode => _replyMode;
  String? get targetCharacterId => _targetCharacterId;
  bool get wsConnected => _wsConnected;

  /// 当前指定角色的名字（自由轮询时为空）
  String get targetName {
    final id = _targetCharacterId;
    if (id == null) return '';
    final members = _current?.members ?? const [];
    for (final m in members) {
      if (m.characterId == id) return m.name;
    }
    return '';
  }

  /// 设定发言对象：null 表示恢复自由轮询
  void setReplyTarget(String? characterId) {
    _targetCharacterId = characterId;
    _replyMode = characterId == null ? 'round_robin' : 'target';
    notifyListeners();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  /// 加载群聊列表
  Future<void> loadGroups() async {
    final groups = await _api.listGroups(ApiConfig.defaultUserId);
    if (groups == null) {
      _error = '群聊列表加载失败，请确认后端服务已启动';
    } else {
      _groups = groups;
      _error = '';
    }
    _notify();
  }

  /// 创建群聊
  Future<GroupInfo?> createGroup(String name, List<String> characterIds) async {
    final group = await _api.createGroup(
      userId: ApiConfig.defaultUserId,
      name: name,
      characterIds: characterIds,
    );
    if (group == null) {
      _error = '创建群聊失败，请检查角色数量（1~4 个）';
      _notify();
      return null;
    }
    await loadGroups();
    return group;
  }

  Future<bool> deleteGroup(String groupId) async {
    final ok = await _api.deleteGroup(ApiConfig.defaultUserId, groupId);
    if (ok) {
      _groups = _groups.where((g) => g.groupId != groupId).toList();
      _notify();
    }
    return ok;
  }

  /// 进入群聊，加载历史并尝试建立 WebSocket（失败则回退 SSE）
  Future<void> openGroup(GroupInfo group) async {
    _current = group;
    _messages = [];
    _error = '';
    _activeIndex = null;
    _pendingRounds = 0;
    _isStreaming = false;
    // 换群后原指定角色可能不在新群里，重置为自由轮询
    _replyMode = 'round_robin';
    _targetCharacterId = null;
    _notify();

    final history = await _api.history(ApiConfig.defaultUserId, group.groupId);
    if (_current?.groupId != group.groupId) return;
    if (history == null) {
      _error = '群聊历史加载失败';
    } else {
      _messages = history;
    }
    _notify();

    // 不阻塞页面渲染：连上之前发送消息走 SSE
    unawaited(_connectWs(group));
  }

  Future<void> _connectWs(GroupInfo group) async {
    final ok = await _ws.connect(
      userId: ApiConfig.defaultUserId,
      groupId: group.groupId,
      onEvent: _handleWsEvent,
      onError: (err) {
        _wsConnected = false;
        _error = '实时连接断开，已回退为普通模式：$err';
        _resetStreaming();
        _notify();
      },
      onClosed: () {
        // 服务端主动断开：复位流式状态，避免输入框一直禁用
        _wsConnected = false;
        _resetStreaming();
        _notify();
      },
    );
    if (_current?.groupId != group.groupId) {
      await _ws.close();
      return;
    }
    _wsConnected = ok;
    _notify();
  }

  void _resetStreaming() {
    _handleTurnDone();
    _activeIndex = null;
    _pendingRounds = 0;
    _isStreaming = false;
  }

  void closeGroup() {
    unawaited(_ws.close());
    _wsConnected = false;
    _current = null;
    _messages = [];
    _error = '';
    _activeIndex = null;
    _pendingRounds = 0;
    _isStreaming = false;
    _notify();
  }

  /// 结束群聊并触发记忆蒸馏（不清空本地消息）
  Future<void> endGroup() async {
    final group = _current;
    if (group == null) return;
    await _api.endGroup(ApiConfig.defaultUserId, group.groupId);
  }

  // ---------- 流式事件（SSE 与 WebSocket 共用）----------

  void _handleStart(String characterId, String name) {
    _messages.add(GroupMessage(
      role: 'assistant',
      content: '',
      characterId: characterId,
      name: name,
      isStreaming: true,
    ));
    _activeIndex = _messages.length - 1;
    _notify();
  }

  void _handleToken(String token) {
    final index = _activeIndex;
    if (index == null || index >= _messages.length) return;
    final msg = _messages[index];
    _messages[index] = msg.copyWith(content: msg.content + token);
    _notify();
  }

  void _handleTurnDone() {
    final index = _activeIndex;
    if (index == null || index >= _messages.length) return;
    _messages[index] = _messages[index].copyWith(isStreaming: false);
    _notify();
  }

  void _handleEnd() {
    _pendingRounds = _pendingRounds > 0 ? _pendingRounds - 1 : 0;
    _isStreaming = _pendingRounds > 0;
    _notify();
  }

  void _handleError(String error) {
    _error = error;
    _resetStreaming();
    _notify();
  }

  void _handleWsEvent(Map<String, dynamic> data) {
    switch (data['type']) {
      case 'start':
        _handleStart(data['character_id'] ?? '', data['name'] ?? '');
        break;
      case 'token':
        _handleToken(data['content'] ?? '');
        break;
      case 'done':
        _handleTurnDone();
        break;
      case 'interrupted':
        // 被打断的角色已停止输出，等待旧轮的 end 收尾
        _handleTurnDone();
        break;
      case 'end':
        _handleEnd();
        break;
      case 'error':
        _handleError(data['message'] ?? '未知错误');
        break;
    }
  }

  /// 在群里发言：优先 WebSocket（可中途插话），未连上时走 SSE
  Future<void> sendMessage(String text) async {
    final group = _current;
    if (group == null || text.trim().isEmpty) return;
    if (_isStreaming && !_wsConnected) return;

    _messages.add(GroupMessage(role: 'user', content: text, name: '我'));
    _pendingRounds++;
    _isStreaming = true;
    _error = '';
    _activeIndex = null;
    _notify();

    if (_wsConnected) {
      _ws.sendMessage(
        content: text,
        replyMode: _replyMode,
        targetCharacterId: _targetCharacterId,
      );
      return;
    }

    await _api.chat(
      userId: ApiConfig.defaultUserId,
      groupId: group.groupId,
      message: text,
      replyMode: _replyMode,
      targetCharacterId: _targetCharacterId,
      onStart: _handleStart,
      onToken: (characterId, token) => _handleToken(token),
      onTurnDone: (characterId) => _handleTurnDone(),
      onEnd: _handleEnd,
      onError: _handleError,
    );

    // 流意外中断时兜底复位
    if (_isStreaming) {
      _pendingRounds = 0;
      _isStreaming = false;
      _notify();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_ws.close());
    _api.dispose();
    super.dispose();
  }
}
