import 'package:flutter/foundation.dart';

/// API 配置
class ApiConfig {
  /// 后端服务地址
  /// - Web / 桌面：localhost
  /// - Android 模拟器：10.0.2.2 指向宿主机
  /// - Android 真机：需改成宿主机的局域网 IP
  static String get baseUrl {
    if (kIsWeb) return 'http://localhost:8000';
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://localhost:8000';
  }

  // 接口路径
  static const String chatSend = '/api/chat/send';
  static const String chatEnd = '/api/chat/end';
  static const String chatHistory = '/api/chat/history';
  static const String characters = '/api/characters';
  static const String longTermMemory = '/api/memory/long-term';
  static const String characterMemory = '/api/memory/character';

  // 默认用户 ID（M0 阶段简化，后续加登录）
  static const String defaultUserId = 'user_001';
}
