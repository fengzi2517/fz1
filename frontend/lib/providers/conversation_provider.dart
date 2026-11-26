import 'package:flutter/foundation.dart';

import '../config/api_config.dart';
import '../models/conversation.dart';
import '../services/conversation_api.dart';

/// 消息页会话列表状态
class ConversationProvider extends ChangeNotifier {
  final ConversationApi _api;

  ConversationProvider({ConversationApi? api}) : _api = api ?? ConversationApi();

  List<ConversationItem> _items = [];
  bool _loading = false;
  String _error = '';

  List<ConversationItem> get items => _items;
  bool get loading => _loading;
  String get error => _error;

  Future<void> load() async {
    _loading = true;
    notifyListeners();
    final items = await _api.list(ApiConfig.defaultUserId);
    if (items == null) {
      _error = '会话列表加载失败，请确认后端服务已启动';
    } else {
      _items = items;
      _error = '';
    }
    _loading = false;
    notifyListeners();
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}
