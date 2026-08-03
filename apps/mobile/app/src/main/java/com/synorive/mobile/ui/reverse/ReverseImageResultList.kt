package com.synorive.mobile.ui.reverse

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.synorive.mobile.data.model.ReverseImageResult
import com.synorive.mobile.ui.camera.ReverseHitRow

/** W5 结果的标准渲染——拍照反查、分享反查、对库里条目反查，三处共用同一套。 */
fun LazyListScope.reverseImageResultItems(result: ReverseImageResult) {
    result.bestGuess?.let { guess ->
        item { Text("看起来像：$guess", style = MaterialTheme.typography.titleMedium) }
    }
    if (result.pagesIncluding.isNotEmpty()) {
        item {
            Text(
                "出现在这些页面",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
            )
        }
        items(result.pagesIncluding) { hit -> ReverseHitRow(hit) }
    }
    if (result.visualSimilar.isNotEmpty()) {
        item {
            Text(
                "视觉相似（不一定是同一张图）",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
            )
        }
        items(result.visualSimilar) { hit -> ReverseHitRow(hit) }
    }
}
