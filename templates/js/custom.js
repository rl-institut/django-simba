function create_chart(data, label, div_id, x_axis_label, y_axis_label) {
    const x_data = data.map(item => item[0]);
    const y_data = data.map(item => item[1]);

    // Create the chart
    const ctx = document.getElementById(div_id).getContext('2d');
    // ctx.height = 400;
    const myChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: x_data,
            datasets: [{
                label: label,
                data: y_data,
                borderColor: 'rgba(75, 192, 192, 1)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                borderWidth: 1
            }]
        },
        options: {
            scales: {
                x: {
                    beginAtZero: true,
                    title: {
                        display: x_axis_label !== undefined && x_axis_label !== null,
                        text: x_axis_label !== undefined && x_axis_label !== null ? x_axis_label : '',
                        color: 'black',
                        font: {
                            size: 14,
                            weight: 'bold'
                        }
                    }
                },
                y: {
                    beginAtZero: true,
                    title: {
                        display: y_axis_label !== undefined && y_axis_label !== null,
                        text: y_axis_label !== undefined && y_axis_label !== null ? y_axis_label : '',
                        color: 'black',
                        font: {
                            size: 14,
                            weight: 'bold'
                        }
                    }

                }
            }
        }
    });
}
