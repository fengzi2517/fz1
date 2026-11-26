import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/api_config.dart';
import '../models/conversation.dart';

/// 会话列表 API
class ConversationApi {
  final http.Client _client = http.Client();

  /// 失败返回 null
  Future<List<ConversationItem>?> list(String userId) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}/api/conversations')
        .replace(queryParameters: {'user_id': userId});
    try {
      final resp = await _client.get(uri);
      if (resp.statusCode == 200) {
        final list = jsonDecode(utf8.decode(resp.bodyBytes)) as List<dynamic>;
        return list
            .map((e) => ConversationItem.fromJson(e as Map<String, dynamic>))
            .toList();
      }
    } catch (_) {
      // 网络异常按失败处理
    }
    return null;
  }

  void dispose() {
    _client.close();
  }
}
