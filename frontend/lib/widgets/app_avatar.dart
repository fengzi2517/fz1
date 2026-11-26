import 'package:flutter/material.dart';

/// 统一头像：按名字哈希稳定取色，群聊显示群组图标
class AppAvatar extends StatelessWidget {
  final String name;
  final double size;
  final bool isGroup;

  const AppAvatar({
    super.key,
    required this.name,
    this.size = 48,
    this.isGroup = false,
  });

  static const _palette = [
    Color(0xFF7C4DFF),
    Color(0xFF00897B),
    Color(0xFFEF6C00),
    Color(0xFF1E88E5),
    Color(0xFFD81B60),
    Color(0xFF43A047),
  ];

  Color get _color {
    if (name.isEmpty) return _palette.first;
    final hash = name.codeUnits.fold<int>(0, (a, b) => a + b);
    return _palette[hash % _palette.length];
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: _color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(size * 0.28),
      ),
      child: isGroup
          ? Icon(Icons.groups, size: size * 0.5, color: _color)
          : Text(
              name.isEmpty ? '?' : name.substring(0, 1),
              style: TextStyle(
                color: _color,
                fontSize: size * 0.4,
                fontWeight: FontWeight.w600,
              ),
            ),
    );
  }
}
