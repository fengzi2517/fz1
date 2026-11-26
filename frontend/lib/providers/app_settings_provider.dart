import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 外观主题色选项
class ThemeOption {
  final String label;
  final Color color;

  const ThemeOption(this.label, this.color);
}

/// 外观设置：主题色、聊天字号，持久化到本地
class AppSettingsProvider extends ChangeNotifier {
  static const List<ThemeOption> themeOptions = [
    ThemeOption('星紫', Color(0xFF7C4DFF)),
    ThemeOption('海蓝', Color(0xFF2F80ED)),
    ThemeOption('森林', Color(0xFF2E9E5B)),
    ThemeOption('落日', Color(0xFFE8734A)),
    ThemeOption('樱粉', Color(0xFFE0528C)),
  ];

  int _themeIndex = 0;
  double _textScale = 1.0;

  Color get seedColor => themeOptions[_themeIndex].color;
  int get themeIndex => _themeIndex;
  double get textScale => _textScale;

  Future<void> init() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _themeIndex = prefs.getInt('app_theme_index') ?? 0;
      _textScale = prefs.getDouble('app_text_scale') ?? 1.0;
      if (_themeIndex < 0 || _themeIndex >= themeOptions.length) {
        _themeIndex = 0;
      }
      notifyListeners();
    } catch (e) {
      debugPrint('[AppSettingsProvider] 读取设置失败: $e');
    }
  }

  Future<void> setThemeIndex(int index) async {
    if (index < 0 || index >= themeOptions.length) return;
    _themeIndex = index;
    notifyListeners();
    await _saveInt('app_theme_index', index);
  }

  Future<void> setTextScale(double scale) async {
    _textScale = scale.clamp(0.85, 1.3);
    notifyListeners();
    await _saveDouble('app_text_scale', _textScale);
  }

  Future<void> _saveInt(String key, int value) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt(key, value);
    } catch (e) {
      debugPrint('[AppSettingsProvider] 保存失败: $e');
    }
  }

  Future<void> _saveDouble(String key, double value) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setDouble(key, value);
    } catch (e) {
      debugPrint('[AppSettingsProvider] 保存失败: $e');
    }
  }
}
